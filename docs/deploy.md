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

## 1. NAS: dataset and NFS share (TrueNAS)

1. **Datasets → Add Dataset** under your pool: `home-brain`, and inside it `vault`.
2. **Shares → UNIX (NFS) Shares → Add**
   - Path: `/mnt/<pool>/home-brain/vault`
   - Networks/Hosts: only the Docker server's IP
   - Advanced → **Mapall User / Mapall Group**: the dataset's owner. Every write from the
     container then lands as that user, so the container's UID doesn't matter.
3. Make sure the NFS service is running with NFSv4 enabled.
4. **Data Protection → Periodic Snapshot Tasks** for `home-brain` (e.g. hourly, keep 2 weeks).
5. Put the NAS IP in `NAS_ADDRESS` and the path in `NAS_VAULT_EXPORT`.

## 2. Move your current notes onto the NAS

Copy **everything** in the local vault folder, including hidden folders like `.git` and
`.trash`, into the dataset. From Windows, an SMB share of the same dataset is easiest:

```powershell
robocopy C:\coding_fun\Code\home-brain\vault \\<nas>\<smb-share> /E
```

After that the NAS copy is the only vault; stop using the local one (step 6 switches Claude
Desktop over).

## 3. GitHub OAuth App

github.com → **Settings → Developer settings → OAuth Apps → New OAuth App**

| Field | Value |
|---|---|
| Application name | home-brain |
| Homepage URL | `https://brain.<domain>` |
| Authorization callback URL | `https://brain.<domain>/auth/callback` |

Register it, copy the **Client ID**, then **Generate a new client secret** and copy that too.
They go in `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`. home-brain only asks GitHub for
`read:user` (who you are), nothing else.

## 4. Cloudflare Tunnel hostname

Cloudflare Zero Trust → **Networks → Tunnels** → your tunnel → **Public Hostname → Add**

- Subdomain `brain`, your domain
- Service: **HTTP**, URL `home-brain:8000` if cloudflared runs as a container on the `proxy`
  Docker network, otherwise `<docker-server-ip>:8000`

Do **not** put a Cloudflare Access application in front of this hostname: Claude's servers
can't get through an Access login. home-brain's GitHub login protects it.

## 5. Run it on the Docker server

```bash
mkdir -p ~/home-brain && cd ~/home-brain
curl -fsSLO https://raw.githubusercontent.com/bjornerikmoen2/home-brain/main/server-docker-compose.yml
curl -fsSL -o .env https://raw.githubusercontent.com/bjornerikmoen2/home-brain/main/.env.example
nano .env   # PUBLIC_URL, GITHUB_*, ALLOWED_USERS, NAS_*
docker compose -f server-docker-compose.yml up -d
docker logs -f home-brain
```

The image is built by GitHub Actions on every push to `main`. If the pull is denied, make the
package public (GitHub → your profile → **Packages → home-brain → Package settings → Change
visibility**) or run `docker login ghcr.io` on the server.

Check from anywhere:

```bash
curl https://brain.<domain>/health        # ok
curl -i https://brain.<domain>/mcp        # 401 with a WWW-Authenticate header
```

Updating later: `docker compose -f server-docker-compose.yml pull && docker compose -f server-docker-compose.yml up -d`.

## 6. Connect Claude

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
| `Permission denied` on `/vault` | NFS share's Mapall user, dataset permissions, Networks/Hosts list |
| Changed `NAS_ADDRESS` / `NAS_VAULT_EXPORT` | `docker compose -f server-docker-compose.yml down && docker volume rm home-brain-vault`, then `up -d` |
| Have to reconnect after every restart | Login state lives in the `home-brain-data` volume; don't delete it |
| Undo a change Claude made | On the NAS or in the container: `git -C /vault log`, then `git -C /vault revert <commit>` |
