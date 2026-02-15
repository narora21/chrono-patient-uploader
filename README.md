# chrono-patient-uploader

Batch upload documents to DrChrono from a directory. Filenames encode the patient name, metatag, date, and document description. Works on Windows and Mac.

## Prerequisites

1. **Register a DrChrono API application** at https://app.drchrono.com/api-management/
2. Set the **Redirect URI** to `http://localhost:8585/callback`
3. Note your **Client ID** and **Client Secret**

## Run with Python

```bash
pip install -r requirements.txt
python src/uploader.py /path/to/documents/
```

On first run you'll be prompted for your Client ID and Client Secret, then a browser window opens for DrChrono login. Tokens are saved to `config.json` for future runs.

## Build standalone executable

```bash
pip install -r requirements.txt
pyinstaller --onefile src/uploader.py
```

This produces:
- **Mac**: `dist/uploader`
- **Windows**: `dist\uploader.exe`

Copy the executable anywhere (keep `metatag.json` and `config.json` next to it) and run it.

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
