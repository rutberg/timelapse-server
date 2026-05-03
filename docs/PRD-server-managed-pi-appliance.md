# PRD: Server-Managed Raspberry Pi Timelapse Appliance

## Summary

This project provides a local-network timelapse server that manages Raspberry Pi camera agents as appliances. The user installs and accesses the server, creates agents through the server web UI, and uses Raspberry Pi Imager only to get each Pi online headlessly. After first boot, the server provisions the Pi over SSH once, installs the agent, and then manages it through an outbound polling model.

The Raspberry Pi is not intended to be directly managed by the user after provisioning.

## Goals

- Run the timelapse server on Debian, ideally in an LXC or similar always-on local machine.
- Provide a web UI for creating, provisioning, monitoring, and controlling Raspberry Pi camera agents.
- Support headless Raspberry Pi setup using Raspberry Pi Imager on macOS, Windows, and Linux.
- Generate per-agent SSH keypairs for secure one-time provisioning.
- Provision agents by hostname/DNS first, with direct IP address fallback.
- Treat the Raspberry Pi as a managed appliance after setup.
- Control capture settings from the server.
- Support server-controlled, agent-pulled updates.
- Keep the system LAN-only by default.

## Non-Goals

- Public cloud hosting.
- Tailscale, VPN, or public inbound access requirements.
- A custom SD-card image builder for the MVP.
- Requiring users to SSH into the Pi manually.
- Long-term SSH as the normal control channel.
- Mobile app support.

## Target Users

- Hobby growers using Raspberry Pi cameras for plant timelapses.
- Home lab users running Debian, Proxmox, LXC, or similar local infrastructure.
- Users comfortable following web-guided setup instructions but not expected to manage Linux on the Pi directly.

## Primary User Flow

1. User installs the timelapse server on Debian.
2. User opens the server web UI.
3. User chooses **Create Agent**.
4. Server starts an agent setup wizard.
5. User selects workstation OS:
   - macOS
   - Windows
   - Linux
6. Server prepares provisioning materials:
   - generates a one-time SSH keypair for this agent setup;
   - stores the private key server-side for provisioning only;
   - displays the public key for Raspberry Pi Imager;
   - shows OS-specific Raspberry Pi Imager instructions;
   - shows the expected hostname, for example `timelapse-tomatoes`;
   - records intended camera ID, server URL, SSH username, and optional IP fallback.
7. In Raspberry Pi Imager, user configures:
   - Raspberry Pi OS Lite 32-bit;
   - hostname, for example `timelapse-tomatoes`;
   - Wi-Fi SSID/password;
   - Wi-Fi country;
   - SSH enabled;
   - SSH authentication using the server-provided public key.
8. User flashes the SD card, inserts it into the Pi, and powers the Pi on.
9. Pi boots and joins the LAN.
10. User returns to the server UI and clicks **Detect / Provision Agent**.
11. Server attempts provisioning:
   - first tries the expected hostname/DNS, for example `timelapse-tomatoes.local`;
   - if that fails, tries the optional IP fallback;
   - if both fail, shows troubleshooting and lets the user enter or update the IP address.
12. Server SSHes into the Pi using the generated private key.
13. Server installs camera dependencies and the timelapse agent.
14. Server writes `/etc/timelapse-agent/config.json`.
15. Server enables and starts `timelapse-agent.service`.
16. Agent checks in to the server.
17. Server marks the agent as online and provisioned.
18. Server deletes or archives the provisioning private key according to the selected policy.
19. From then on, the server manages the Pi through polling-based desired state.

## Key Product Principles

- The server is the control plane.
- Raspberry Pi Imager is used only to get the Pi online headlessly.
- The Pi becomes an appliance after provisioning.
- The user should not need HDMI, keyboard, mouse, or direct Pi shell access.
- The agent should initiate normal communication with the server.
- DHCP changes should not break normal operation after provisioning.
- SSH is a provisioning mechanism, not the long-term control path.

## Server Responsibilities

The server must:

- provide the web UI and API;
- manage desired camera configuration;
- store uploaded images;
- generate timelapse videos;
- create pending agent records;
- generate per-agent SSH provisioning keypairs;
- guide users through Raspberry Pi Imager setup;
- provision Pi agents over SSH;
- track agent status and last check-in;
- expose update metadata for agent-pulled updates;
- enforce LAN-only access by default.

## Agent Responsibilities

The agent must:

- run as a systemd service on the Raspberry Pi;
- poll the server for desired state;
- capture images using Raspberry Pi camera tools;
- upload images to the server;
- report status, version, and last error;
- apply server-controlled configuration changes;
- perform agent-pulled updates when instructed by the server.

## Provisioning Inputs

The server UI should collect or generate:

- `camera_id`;
- display name;
- expected hostname, for example `timelapse-tomatoes.local`;
- optional direct IP fallback;
- SSH username;
- generated SSH public key;
- generated SSH private key stored server-side;
- server URL to write into agent config;
- optional “disable SSH after provisioning” setting;
- optional “keep debug SSH access” setting.

## Hostname, DNS, and DHCP Strategy

### Normal Operation

The system should not depend on the server finding the agent after provisioning.

- Pi receives any DHCP address.
- Agent boots and calls the server URL.
- Server records the request source IP.
- Server stores last seen timestamp, hostname, and status.
- Server controls the Pi through desired state returned during agent polling.

### Provisioning

Provisioning requires finding the Pi once.

Resolution order:

1. Try expected hostname/DNS, for example `timelapse-tomatoes.local`.
2. If that fails, try optional direct IP address fallback.
3. If both fail, show troubleshooting and allow the user to update the IP.

Troubleshooting should mention:

- confirm the Pi has power;
- wait for first boot to finish;
- confirm Wi-Fi SSID is 2.4 GHz compatible for Pi Zero W;
- confirm Wi-Fi country was set correctly;
- confirm SSH was enabled in Raspberry Pi Imager;
- check router or DHCP leases for the Pi hostname;
- enter the Pi IP manually as fallback.

### Server Addressing

The server should have a stable address. Recommended options:

- DHCP reservation for the server IP;
- local DNS name;
- static server IP if appropriate.

The agent config should contain a stable `server_url`, for example:

```json
{
  "server_url": "http://192.168.68.52:8080"
}
```

## SSH Key Policy

Default policy:

- Generate one SSH keypair per pending agent.
- Use the private key only for first provisioning.
- Do not ask users for Pi passwords in the default flow.
- After successful agent check-in:
  - remove the private key from active provisioning storage;
  - optionally archive encrypted provisioning metadata for audit/debugging;
  - optionally remove the authorized public key from the Pi;
  - optionally disable SSH for appliance mode.

Fallback policy:

- Password-based provisioning may be supported later.
- Passwords must never be persisted.
- Passwords may only be held in memory for the active provisioning attempt.
- UI must clearly label this as a fallback for trusted LANs.

## Agent Configuration

The server writes `/etc/timelapse-agent/config.json` during provisioning.

Example:

```json
{
  "camera_id": "timelapse-tomatoes",
  "server_url": "http://192.168.68.52:8080",
  "config_poll_seconds": 60,
  "work_dir": "/var/lib/timelapse-agent"
}
```

## Server-Controlled Agent Settings

The server should control:

- enabled/paused state;
- capture interval;
- image width;
- image height;
- full-resolution mode using nullable width/height;
- JPEG quality;
- capture-now request;
- desired agent version;
- diagnostics request.

## Agent Status Model

The server should track:

- camera ID;
- display name;
- provisioning status;
- configured hostname;
- last source IP;
- last seen timestamp;
- current agent version;
- desired agent version;
- capture status;
- last capture timestamp;
- last upload timestamp;
- last update status;
- last error;
- disk usage if reported;
- Wi-Fi signal if reported;
- camera detection status if reported.

## Update Architecture

Updates should be server-controlled but agent-pulled.

Flow:

1. Server stores desired agent version.
2. Agent polls server and sees a newer desired version.
3. Agent downloads update manifest/package from server.
4. Agent verifies checksum.
5. Agent installs the update.
6. Agent restarts its systemd service.
7. Agent reports success or failure.

Rationale:

- avoids keeping SSH open forever;
- works if the Pi IP changes;
- survives Wi-Fi reconnects;
- allows retry and status reporting;
- keeps the Pi appliance-like.

## Image Capture and Upload

The agent should:

- capture full-resolution images by default;
- support optional server-specified width/height limits;
- save failed-upload images locally until upload succeeds;
- upload images with capture timestamps;
- retry uploads without losing captures.

The server should:

- store images by camera and date;
- expose latest image preview;
- generate videos on demand;
- later support daily and weekly scheduled video generation.

## Security Requirements

- LAN-only access by default.
- No public exposure by default.
- No long-term SSH requirement.
- No default password-based provisioning.
- No persistent Wi-Fi credential storage on the server.
- Provisioning private keys are per-agent and temporary by default.
- Server UI should eventually require local admin authentication.
- Provisioning should validate and sanitize all user-provided host/IP/camera fields.
- Shell commands must not be built from unsanitized user input.

## MVP Scope

- Debian server install script.
- FastAPI server API.
- Server-side camera config storage.
- Agent polling, capture, upload, and retry queue.
- Server-side image storage.
- On-demand video generation with `ffmpeg`.
- README-guided Raspberry Pi Imager setup for macOS, Windows, and Linux.
- CLI or API-based one-time SSH provisioning using generated SSH keypair.
- Hostname-first/IP-fallback provisioning.
- Basic agent check-in/status tracking.

## Phase 2 Scope

- Full web UI for Create Agent wizard.
- Web UI for provisioning state and troubleshooting.
- Web UI for camera settings.
- Latest-image preview.
- Capture-now button.
- Agent update pipeline.
- Daily and weekly video jobs.
- Diagnostics reporting.
- Optional SSH disable-after-provisioning.
- Multiple cameras/agents per server.

## Out of Scope For Now

- Custom SD image builder hosted on the server.
- Browser-based video editor.
- Remote access outside LAN.
- Multi-user cloud accounts.
- Native mobile app.

## Open Questions

- Should SSH be disabled automatically after successful provisioning and check-in?
- Should the provisioning private key be deleted immediately or retained encrypted for a short recovery window?
- Should password-based SSH provisioning exist at all?
- Should mDNS support be required in the Debian LXC, or should manual IP fallback be the official fallback?
- Should agent updates be Git-based, release-bundle-based, or package-based?
- Should update bundles be signed in MVP or Phase 2?
