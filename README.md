# chrono-patient-uploader

Batch upload documents to DrChrono from a directory. Filenames encode the patient name, metatag, date, and document description. Works on Mac, Linux, and Windows.

## Install

### Mac / Linux

```sh
curl -fsSL https://raw.githubusercontent.com/narora21/chrono-patient-uploader/main/install.sh | sh
```

Installs to `~/chrono-uploader/`. To add it to your PATH:

```sh
export PATH="$HOME/chrono-uploader:$PATH"
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/narora21/chrono-patient-uploader/main/install.ps1 | iex
```

Installs to `%LOCALAPPDATA%\chrono-uploader\`.

## Prerequisites

1. **Register a DrChrono API application** at https://app.drchrono.com/api-management/
2. Set the **Redirect URI** to `http://localhost:8585/callback`
3. Note your **Client ID** and **Client Secret**

On first run you'll be prompted for your Client ID and Client Secret, then a browser window opens for DrChrono login. Tokens are saved to `config.json` for future runs.

## Usage

```bash
chrono-uploader /path/to/documents/
```

### Options

| Flag | Description |
|---|---|
| `--dry-run` | Parse and validate files without uploading or moving |
| `--dest DIR` | Move successfully uploaded files to this directory |
| `--pattern PATTERN` | Filename pattern using placeholders (default: `{name}_{tag}_{date}_{description}`) |
| `--num-workers N` | Number of parallel upload workers (default: 1) |

## Filename format

Files must be named as:

```
LAST,FIRST_TAG_MMDDYY_DESCRIPTION.pdf
```

With optional middle initial:

```
LAST,FIRST M_TAG_MMDDYY_DESCRIPTION.pdf
```

### Examples

| Filename | Patient | Tag | Date | Description |
|---|---|---|---|---|
| `DOE,JANE_R_020326_CXR.pdf` | Jane Doe | R (radiology) | 2026-02-03 | CXR |
| `DOE,JANE M_HP_011525_CONSULT_NOTE.pdf` | Jane M Doe | HP (h&p/consults) | 2025-01-15 | CONSULT_NOTE |
| `SMITH,JOHN_L_120124_CBC.pdf` | John Smith | L (laboratory) | 2024-12-01 | CBC |

### Custom patterns

Use `--pattern` to change the expected filename structure. Available placeholders:

- `{name}` — `LAST,FIRST[ M]` (comma-separated, with optional middle initial)
- `{last_name}`, `{first_name}`, `{middle_initial}` — individual name fields
- `{tag}` — metatag code (must match a key in `metatag.json`)
- `{date}` — date in MMDDYY format
- `{description}` — document description

Example: `--pattern "{last_name}_{first_name}_{tag}_{date}_{description}"` for files like `DOE_JANE_R_020326_CXR.pdf`.

## Metatag config

Edit `metatag.json` to configure tag codes:

```json
{
  "L": "laboratory",
  "R": "radiology",
  "HP": "h&p/consults",
  "C": "cardiology",
  "P": "pulmonary",
  "CO": "correspondence",
  "MI": "miscellaneous",
  "D": "demographics",
  "M": "medications/rx"
}
```

## Config

Credentials and tokens are stored in `config.json` next to the executable. Add `config.json` to your `.gitignore` — it contains secrets.

## Development

### Run from source

```bash
pip install -r requirements.txt
python -m src.main /path/to/documents/
```

### Run tests

```bash
make test
```

### Build standalone executable

```bash
make build
```

Produces `dist/chrono-uploader` (Mac/Linux) or `dist\chrono-uploader.exe` (Windows).

### Create distributable archive

```bash
make dist
```

Produces a `.tar.gz` (Mac/Linux) or `.zip` (Windows) containing the executable, `metatag.json`, and `README.md`.

### Release a new version

1. Tag the commit:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
2. GitHub Actions automatically builds executables for Mac, Linux, and Windows, then creates a GitHub Release with all three archives attached.
3. The install scripts (`install.sh` / `install.ps1`) always pull from the latest release — no URL changes needed.
