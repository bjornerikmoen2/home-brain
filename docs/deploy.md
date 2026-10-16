# Deploying home-brain (Phase 1)

The end result:

```
Claude app (web / desktop / phone)
   │  custom connector: https://brain.<domain>/mcp, GitHub login
   ▼
Cloudflare Tunnel ──► home-brain container (Docker server)
                          │  every change = one git commit
                          ▼
                       vault on the NAS (NFS), ZFS snapshots
```

Only the GitHub logins in `ALLOWED_USERS` get access. Anyone else can complete the GitHub
login but sees no tools.

Not part of this step yet: pushing the vault's git history to a private GitHub repo. Until then,
ZFS snapshots on the NAS are the backup.

All site-specific values go in `.env` on the server (copy `.env.example`), never in this repo.

## 1. NAS: user, dataset and NFS share (TrueNAS)

Menu names are for TrueNAS CORE; on SCALE they are Credentials → Users, Datasets, Shares and
Data Protection.

1. **Accounts → Users → Add**: username `homebrain`, *New Primary Group* on, *Disable Password*,
   shell `nologin`, home `/nonexistent`, *Samba Authentication* off. Note its **UID** and the
   `homebrain` group's **GID**: they go in `HOME_BRAIN_UID` / `HOME_BRAIN_GID`, so the container
   runs as the user that owns the files.
2. **Storage → Pools →** ⋮ on the pool **→ Add Dataset** `home-brain` (*Share Type*: **Generic**,
   not SMB), then inside it a dataset `vault` (Generic too).
3. ⋮ on `vault` **→ Edit Permissions**: user and group `homebrain` (tick both *Apply*), mode
   `770`.
4. **Sharing → Unix Shares (NFS) → Add**
   - Path: `/mnt/<pool>/home-brain/vault`
   - Advanced → *Mapall User* / *Mapall Group*: `homebrain`
   - Advanced → *Authorized Hosts and IP addresses*: only the Docker server's IP
5. The NFS service must be running. home-brain mounts with **NFSv3** by default
   (`NAS_NFS_VERSION=3`), which needs no extra settings. Only if you want NFSv4: enable it in the
   NFS service (on CORE also *NFSv3 ownership model for NFSv4*) and set `NAS_NFS_VERSION=4.1`.
   Changing NFS service settings restarts it and briefly stalls every other NFS client.
6. **Tasks → Periodic Snapshot Tasks → Add**: dataset `<pool>/home-brain`, recursive, hourly,
   keep 2 weeks (optionally a daily task kept 3 months).
7. Put the NAS IP in `NAS_ADDRESS` and the path in `NAS_VAULT_EXPORT`.

## 2. GitHub OAuth App

github.com → **Settings → Developer settings → OAuth Apps → New OAuth App**

| Field | Value |
|---|---|
| Application name | home-brain |
| Homepage URL | `https://brain.<domain>` |
| Authorization callback URL | `https://brain.<domain>/auth/callback` |

Register it, copy the **Client ID**, then **Generate a new client secret** and copy that too.
They go in `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`. home-brain only asks GitHub for
`read:user` (who you are), nothing else.

## 3. Run it on the Docker server

```bash
mkdir -p /opt/docker/home-brain && cd /opt/docker/home-brain
curl -fsSL -o docker-compose.yml https://raw.githubusercontent.com/bjornerikmoen2/home-brain/main/server-docker-compose.yml
curl -fsSL -o .env https://raw.githubusercontent.com/bjornerikmoen2/home-brain/main/.env.example
chmod 600 .env
nano .env   # PUBLIC_URL, GITHUB_*, ALLOWED_USERS, NAS_*, HOME_BRAIN_UID/GID
docker compose pull
docker compose create        # creates the NFS volume and the container, without starting it
```

**Bringing existing notes?** Copy them in now, *before* the first start. An empty vault gets the
starter notes and a fresh git repository on first start, and copying another vault over that
would mix two histories. Copy everything, including hidden folders like `.git`:

```bash
# on your PC
scp -r C:\coding_fun\Code\home-brain\vault <user>@<docker-server>:/tmp/vault-import
# on the server, as the vault owner (plain cp -r: the NAS won't accept other owners)
docker run --rm -u <uid>:<gid> -v home-brain-vault:/vault:nocopy -v /tmp/vault-import:/import:ro alpine cp -r /import/. /vault/
rm -rf /tmp/vault-import
```

Then start it:

```bash
docker compose up -d
docker logs -f home-brain
curl -s http://127.0.0.1:8000/health   # ok
```

The container joins the external `proxy` network (create it with `docker network create proxy`
if it doesn't exist) and is published on `127.0.0.1` only. The image
`ghcr.io/bjornerikmoen2/home-brain` is built by GitHub Actions on every push to `main`.
After moving your notes, the NAS copy is the only vault; stop using the local one (step 5
switches Claude Desktop over).

Updating later: `docker compose pull && docker compose up -d`.

## 4. Cloudflare Tunnel hostname

The tunnel forwards `brain.<domain>` to `http://home-brain:8000` over the `proxy` network.

- **Tunnel managed in the dashboard:** Zero Trust → **Networks → Tunnels** → your tunnel →
  **Public Hostname → Add**: subdomain `brain`, service **HTTP** `home-brain:8000`.
- **Tunnel managed with `config.yml`:** add an ingress rule above the final `http_status:404`
  rule, create the DNS record, and restart cloudflared:

  ```yaml
  - hostname: brain.<domain>
    service: http://home-brain:8000
  ```

  ```bash
  cloudflared tunnel route dns <tunnel-name> brain.<domain>
  ```

Do **not** put a Cloudflare Access application in front of this hostname: Claude's servers
can't get through an Access login. home-brain's GitHub login protects it.

Check from anywhere:

```bash
curl https://brain.<domain>/health        # ok
curl -i https://brain.<domain>/mcp        # 401 with a WWW-Authenticate header
```

## 5. Connect Claude

1. claude.ai → **Settings → Connectors → Add custom connector**
   - Name: `home-brain`
   - URL: `https://brain.<domain>/mcp`
2. **Connect** → home-brain's consent page → **Allow Access** → sign in to GitHub → authorize.
3. The connector now works in Claude on the web, in Claude Desktop and on the phone.
4. Remove the old local `home-brain` entry from `claude_desktop_config.json`, so Claude Desktop
   doesn't keep using the local vault.

## Troubleshooting

| Symptom | Check |
|---|---|
| "Authorization with the MCP server failed" | `PUBLIC_URL` is exactly `https://brain.<domain>` (no trailing slash); the GitHub app's callback is `PUBLIC_URL/auth/callback`; `docker logs home-brain` |
| Connected, but no tools | Your GitHub login isn't in `ALLOWED_USERS`; the log says `denied GitHub user` |
| Container won't start: `mount ... failed` | The NFS export path, *Authorized Hosts* on the share, `NAS_NFS_VERSION` (NFSv4 must be enabled on the NAS for `4.1`) |
| `Permission denied` on `/vault` | `HOME_BRAIN_UID/GID` match the dataset owner; the share's *Mapall* user; dataset mode `770` |
| Changed `NAS_*` in `.env` | `docker compose down && docker volume rm home-brain-vault`, then `docker compose up -d` |
| Have to reconnect after every restart | Login state lives in the `home-brain-data` volume; don't delete it |
| Undo a change Claude made | `docker exec home-brain git -C /vault log`, then `docker exec home-brain git -C /vault revert <commit>` |
