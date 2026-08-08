# Tailscale for LibreELEC

This Kodi service add-on runs the official [`tailscale/tailscale`](https://hub.docker.com/r/tailscale/tailscale) Docker image on LibreELEC.

## Requirements

- LibreELEC with the **Docker (`service.system.docker`)** add-on installed and enabled. Kodi declares this add-on as a required dependency and should install it automatically when available from the configured LibreELEC repository.
- A LibreELEC installation where the Docker CLI can be called as `docker`.
- Network access from the host to pull the configured image.
- For normal kernel networking, `/dev/net/tun` must be available. Enable **Use userspace networking** if the host cannot provide TUN access; userspace mode has fewer privileges but does not provide the same transparent host networking behavior.
- ConnMan, which is the network manager used by LibreELEC.

## Installation

1. Build or zip this directory as a Kodi add-on, preserving the `service.tailscale/addon.xml` directory structure in the archive.
2. Install the resulting ZIP from **Settings → Add-ons → Install from zip file**.
3. Ensure the LibreELEC Docker (`service.system.docker`) add-on is installed and enabled. It is declared as a dependency in `addon.xml`.
4. Open **Services → Tailscale** and configure the add-on.

For GitHub Actions, use the asset attached to a GitHub Release, named like `service.tailscale-0.1.0.zip`. Do not use an old workflow artifact that was uploaded before the artifact fix. Current CI artifacts are named `kodi-addon-files-<version>` and contain the extracted add-on files; GitHub wraps workflow artifacts in a download ZIP, so they must not be confused with the direct release asset.

The service creates a container named `kodi-tailscale`. Tailscale state is persisted in:

```text
/storage/.kodi/userdata/addon_data/service.tailscale/state
```

The state directory prevents the device identity from changing whenever the container is recreated.

## LibreELEC DNS workaround

LibreELEC's `/etc/resolv.conf` is read-only. As a result, Tailscale cannot replace the host resolver with its MagicDNS resolver at `100.100.100.100`. This add-on works around that limitation by polling ConnMan's active services and applying all of the following nameservers:

```sh
connmanctl config <active-service> nameservers 100.100.100.100 8.8.8.8 1.1.1.1
```

`100.100.100.100` provides Tailscale MagicDNS, while `8.8.8.8` and `1.1.1.1` remain available for ordinary DNS lookups such as LibreELEC NTP host resolution. The check runs at startup, after active service changes, and periodically to recover from ConnMan resetting a service's DNS configuration. Both `ready` and `online` ConnMan services are handled, so wired/wireless failover is supported. Disable **Configure ConnMan for Tailscale MagicDNS** only if DNS is managed elsewhere.

The workaround intentionally does not attempt to restore previous nameservers when disabled or when the add-on is removed, because those values may have been supplied dynamically by DHCP. Reconnect the network service or configure its DNS through ConnMan if restoration is required.

## Authentication

Provide a Tailscale auth key in the add-on settings for unattended startup. The key is passed to Docker only when the container is created and is not written to the add-on's configuration file. Docker itself stores container environment variables in its metadata, so use a short-lived or otherwise appropriately scoped Tailscale auth key.

If no auth key is configured, the container starts unauthenticated. Open the container logs to obtain the interactive login URL:

```sh
docker logs kodi-tailscale
```

Create auth keys in the Tailscale admin console and follow Tailscale's key-scope and expiry recommendations.

## Configuration changes

Changing the image, hostname, networking mode, DNS mode, extra arguments, or auth key causes the managed container to be recreated. Persistent state is retained. The container is stopped when Kodi shuts down and is started again with the service.

The **Tailscale extra arguments** field is passed to the image as `TS_EXTRA_ARGS`; for example:

```text
--advertise-exit-node
```

Review the Tailscale Docker documentation before enabling subnet routes, exit-node behavior, or other privileged features.

## Security notes

The default configuration uses host networking, `/dev/net/tun`, `NET_ADMIN`, and `NET_RAW`, as required for a full Tailscale node in a container. This add-on is intended for a trusted LibreELEC installation. Userspace networking reduces the required kernel networking functionality but changes how traffic is exposed.

This project is an add-on wrapper and is not affiliated with Tailscale, Inc.

## CI/CD and releases

The GitHub Actions release workflow publishes the installable Kodi package as:

```text
service.tailscale-<version>.zip
```

GitHub also automatically displays `Source code (zip)` and `Source code (tar.gz)` for every release. Those are GitHub-generated repository snapshots and are not the Kodi add-on. Install the explicitly uploaded `service.tailscale-<version>.zip` asset instead.

To repair an existing release, manually run **Actions → Release add-on → Run workflow** and enter its tag, for example `v0.1.0`. If the release already exists, the workflow uploads or replaces the versioned Kodi ZIP asset with `--clobber`.

For a tag-triggered release, commit the version change and push the matching tag:

```sh
git add addon.xml
git commit -m "Release v0.1.3"
git tag v0.1.3
git push origin main
git push origin v0.1.3
```

For a manually dispatched release, enter a tag matching the `addon.xml` version, such as `v0.1.3`. If that tag does not exist, the workflow verifies the version, creates the tag on the current default branch, and continues automatically. The workflow will reject a tag whose version does not match `addon.xml`.