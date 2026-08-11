#!/usr/bin/env python3
"""Kodi service which manages Tailscale's official Docker container."""

import hashlib
import json
import os
import re
import subprocess
import sys
import time

import xbmc
import xbmcaddon
import xbmcvfs


ADDON_ID = "service.tailscale"
CONTAINER_NAME = "kodi-tailscale"
DEFAULT_IMAGE = "tailscale/tailscale:stable"
STATE_DIR = "/var/lib/tailscale"
TAILSCALE_DNS = "100.100.100.100"
CONNMAN_NAMESERVERS = ["100.100.100.100", "8.8.8.8", "1.1.1.1"]
CONNMAN_POLL_INTERVAL = 5
CONNMAN_REAPPLY_INTERVAL = 60
IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./:_@-]*$")


class TailscaleService:
    """Keep one Tailscale container running for the lifetime of Kodi."""

    def __init__(self):
        self.addon = xbmcaddon.Addon(ADDON_ID)
        self.monitor = xbmc.Monitor()
        self.data_dir = xbmcvfs.translatePath(
            "special://profile/addon_data/{0}".format(ADDON_ID)
        )
        self.host_state_dir = os.path.join(self.data_dir, "state")
        self.config_path = os.path.join(self.data_dir, "container.json")
        self.active_connman_services = None
        self.last_connman_apply = 0

    def log(self, message, level=xbmc.LOGINFO):
        xbmc.log("[{0}] {1}".format(ADDON_ID, message), level)

    def setting(self, name, default=""):
        value = self.addon.getSetting(name)
        return value if value != "" else default

    def setting_bool(self, name, default=False):
        value = self.addon.getSetting(name)
        if value == "":
            return default
        return value.lower() in ("true", "1", "yes", "on")

    def docker(self, arguments, check=False, environment=None):
        """Run Docker without a shell so settings cannot become shell syntax."""
        try:
            result = subprocess.run(
                ["docker"] + arguments,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=False,
                env=environment,
            )
        except OSError as error:
            self.log("Docker is not available: {0}".format(error), xbmc.LOGERROR)
            return None

        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            self.log(
                "Docker command failed ({0}): {1}".format(result.returncode, detail),
                xbmc.LOGERROR,
            )
            return None
        return result

    def connmanctl(self, arguments, check=False):
        """Run connmanctl without a shell and return its completed process."""
        try:
            result = subprocess.run(
                ["connmanctl"] + arguments,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=False,
            )
        except OSError as error:
            self.log("connmanctl is not available: {0}".format(error), xbmc.LOGWARNING)
            return None

        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            self.log(
                "connmanctl command failed ({0}): {1}".format(
                    result.returncode, detail
                ),
                xbmc.LOGWARNING,
            )
            return None
        return result

    def active_connman_service_ids(self):
        """Return ConnMan service IDs whose state is ready or online."""
        result = self.connmanctl(["services"])
        if result is None or result.returncode != 0:
            return None

        active_services = set()
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 2:
                continue

            flags = fields[0].lstrip("*")
            if not flags or ("R" not in flags and "O" not in flags):
                continue

            service_id = fields[-1]
            details = self.connmanctl(["services", service_id])
            if details is None or details.returncode != 0:
                continue

            state_match = re.search(
                r"^\s*State\s*=\s*(online|ready)\s*$",
                details.stdout,
                re.MULTILINE | re.IGNORECASE,
            )
            if state_match:
                active_services.add(service_id)

        return active_services

    def configure_connman_dns_if_needed(self, force=False):
        """Point active ConnMan services at Tailscale's MagicDNS resolver."""
        if not self.setting_bool("configure_connman_dns", True):
            return

        active_services = self.active_connman_service_ids()
        if active_services is None:
            return

        now = time.monotonic()
        network_changed = active_services != self.active_connman_services
        periodic_reapply = now - self.last_connman_apply >= CONNMAN_REAPPLY_INTERVAL
        if not force and not network_changed and not periodic_reapply:
            return

        self.active_connman_services = active_services
        self.last_connman_apply = now
        for service_id in sorted(active_services):
            result = self.connmanctl(
                ["config", service_id, "nameservers"] + CONNMAN_NAMESERVERS,
                check=True,
            )
            if result is not None:
                self.log(
                    "Configured ConnMan service {0} to use nameservers {1}".format(
                        service_id, ", ".join(CONNMAN_NAMESERVERS)
                    )
                )

    def read_container(self):
        result = self.docker(["inspect", CONTAINER_NAME])
        if result is None or result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout)[0]
        except (ValueError, IndexError, TypeError):
            self.log("Docker returned invalid container information", xbmc.LOGERROR)
            return None

    def read_saved_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as config_file:
                return json.load(config_file)
        except (OSError, ValueError):
            return None

    def write_saved_config(self, config):
        os.makedirs(self.data_dir, exist_ok=True)
        temporary_path = self.config_path + ".tmp"
        with open(temporary_path, "w", encoding="utf-8") as config_file:
            json.dump(config, config_file, indent=2, sort_keys=True)
            config_file.write("\n")
        os.replace(temporary_path, self.config_path)

    def current_config(self):
        auth_key = self.setting("auth_key").strip()
        return {
            "image": self.setting("image", DEFAULT_IMAGE).strip(),
            "hostname": self.setting("hostname").strip(),
            "userspace": self.setting_bool("userspace"),
            "accept_dns": self.setting_bool("accept_dns", True),
            "extra_args": self.setting("extra_args").strip(),
            # Never persist the auth key itself; only use its fingerprint to
            # detect a deliberate credential change.
            "auth_key_sha256": hashlib.sha256(
                auth_key.encode("utf-8")
            ).hexdigest()
            if auth_key
            else "",
        }

    def validate_config(self, config):
        image = config["image"]
        if not image or not IMAGE_PATTERN.match(image):
            self.log(
                "Invalid Docker image name. Using {0}.".format(DEFAULT_IMAGE),
                xbmc.LOGWARNING,
            )
            config["image"] = DEFAULT_IMAGE

    def remove_container(self):
        self.log("Removing the existing Tailscale container")
        self.docker(["rm", "--force", CONTAINER_NAME], check=True)

    def run_container(self, config):
        os.makedirs(self.host_state_dir, exist_ok=True)

        arguments = [
            "run",
            "--detach",
            "--name",
            CONTAINER_NAME,
            "--restart",
            "unless-stopped",
            "--network",
            "host",
            "--cap-add",
            "NET_ADMIN",
            "--cap-add",
            "NET_RAW",
            "--device",
            "/dev/net/tun:/dev/net/tun",
            "--mount",
            "type=bind,src={0},dst={1}".format(self.host_state_dir, STATE_DIR),
            "--env",
            "TS_STATE_DIR={0}".format(STATE_DIR),
            "--env",
            "TS_USERSPACE={0}".format(str(config["userspace"]).lower()),
            "--env",
            "TS_ACCEPT_DNS={0}".format(str(config["accept_dns"]).lower()),
            "--label",
            "com.kodi.addon={0}".format(ADDON_ID),
        ]

        if config["hostname"]:
            arguments += ["--env", "TS_HOSTNAME={0}".format(config["hostname"])]

        auth_key = self.setting("auth_key").strip()
        environment = None
        if auth_key:
            # Keep the key out of the process argument list. Docker still stores
            # it in container metadata, as required by the image's env-based
            # authentication flow.
            environment = os.environ.copy()
            environment["TS_AUTHKEY"] = auth_key
            arguments += ["--env", "TS_AUTHKEY"]

        if config["extra_args"]:
            arguments += ["--env", "TS_EXTRA_ARGS={0}".format(config["extra_args"])]

        arguments.append(config["image"])
        # Do not log arguments: they can contain the auth key.
        result = self.docker(arguments, check=True, environment=environment)
        if result is None:
            return False

        self.write_saved_config(config)
        self.log("Tailscale container started")
        return True

    def ensure_container(self):
        config = self.current_config()
        self.validate_config(config)
        container = self.read_container()

        if container is not None:
            labels = container.get("Config", {}).get("Labels", {}) or {}
            if labels.get("com.kodi.addon") != ADDON_ID:
                self.log(
                    "Container name '{0}' is already in use by another container".format(
                        CONTAINER_NAME
                    ),
                    xbmc.LOGERROR,
                )
                return False

            saved_config = self.read_saved_config()
            if saved_config != config:
                self.remove_container()
                container = None

        if container is None:
            return self.run_container(config)

        if not container.get("State", {}).get("Running", False):
            if self.docker(["start", CONTAINER_NAME], check=True) is None:
                return False
            self.log("Tailscale container started")
        return True

    def stop_container(self):
        container = self.read_container()
        if container is not None and container.get("State", {}).get("Running", False):
            self.log("Stopping Tailscale container")
            self.docker(["stop", "--time", "10", CONTAINER_NAME], check=True)

    def run(self):
        if not self.setting_bool("enabled", True):
            self.log("Add-on is disabled in settings")
            container = self.read_container()
            if container is not None:
                labels = container.get("Config", {}).get("Labels", {}) or {}
                if labels.get("com.kodi.addon") == ADDON_ID:
                    self.stop_container()
        else:
            # Apply ConnMan DNS before starting Tailscale. This is especially
            # important on a fresh boot, when the container could otherwise
            # attempt its first control-plane lookup with the old resolver.
            self.configure_connman_dns_if_needed(force=True)
            if not self.ensure_container():
                self.log("Tailscale could not be started", xbmc.LOGERROR)
            else:
                self.log("Tailscale service is running")

        while not self.monitor.abortRequested():
            self.configure_connman_dns_if_needed()
            self.monitor.waitForAbort(CONNMAN_POLL_INTERVAL)

        self.stop_container()


if __name__ == "__main__":
    try:
        TailscaleService().run()
    except Exception as error:  # Keep an unexpected service failure in Kodi's log.
        xbmc.log("[{0}] Unhandled error: {1}".format(ADDON_ID, error), xbmc.LOGERROR)
        sys.exit(1)