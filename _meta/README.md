# Meta Data Directory - Auto-generated Files

⚠️ **IMPORTANT: This directory contains auto-generated files**

## What's here?

This directory (`_meta/`) contains **runtime-generated metadata** files:

- `catalog.parquet` - Index of all data files (auto-scanned)
- `main_contracts_auto.yaml` - Auto-generated main contract configuration
- Other runtime metadata files

## Key Characteristics

- 🔁 **Auto-generated**: These files are created by tools/scripts
- 🔄 **Can be regenerated**: You can delete and rebuild them anytime
- 🚫 **Do NOT commit to Git**: This directory should be in `.gitignore`
- ⚙️ **Runtime dependency**: Required for factor production

## How to regenerate?

If you need to regenerate these files:

```bash
# Generate catalog and main contracts
python tools/build_catalog_standalone.py
```

## For Manual Configuration

If you're looking for **manual configuration files**, they are in:

👉 **`config/`** directory

- `config/project.yaml` - Project configuration template
- `config/relations.yaml` - Cross-variety relationships
- `config/main_contracts.example.yaml` - Example main contract format

---

**Generated automatically, do not edit manually (unless you know what you're doing)**
