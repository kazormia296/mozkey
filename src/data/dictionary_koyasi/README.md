# Koyasi Dictionary Data

This directory contains additional dictionary data and generated dictionary artifacts for this Mozc fork.

## Policy

- Do not edit upstream dictionary files directly when possible.
- Generated dictionary files are not committed by default.
- External dictionaries must be imported through scripts.
- Large dictionaries should be separated by profile, such as safe, developer, subculture, and max.

## Generated files

generated/ is ignored by Git. Run import scripts to regenerate files locally.

## Notes for Windows PowerShell

Generated dictionary files are UTF-8. Use the following command to inspect them:

```powershell
Get-Content -Encoding UTF8 src/data/dictionary_koyasi/generated/mozcdic-ut-safe.txt -TotalCount 10
```
