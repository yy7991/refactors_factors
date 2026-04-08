# Configuration Directory Separation Summary

## ✅ Decision: Keep `_meta/` and `config/` Separate

After thorough analysis, we've decided to **maintain the separation** between `_meta/` and `config/` directories, with clear responsibility boundaries.

---

## 📁 Final Structure

```
project/
├── config/                      # ✅ Manual Configuration (COMMIT TO GIT)
│   ├── project.yaml            # Project settings template
│   ├── relations.yaml          # Cross-variety relationships
│   └── main_contracts.example.yaml  # Format reference template
│
├── _meta/                       # ✅ Auto-generated Metadata (DO NOT COMMIT)
│   ├── catalog.parquet         # Data file index (6,752 records)
│   ├── main_contracts_auto.yaml    # Auto-generated contracts
│   └── README.md               # Documentation
│
└── .gitignore                   # Git ignore rules (_meta/ excluded)
```

---

## 🎯 Rationale

### Why Separate?

| Aspect | `config/` | `_meta/` | Benefit |
|--------|-----------|----------|---------|
| **Content Type** | Manual templates | Auto-generated data | Clear separation of concerns |
| **Update Frequency** | Rare (manual edits) | Frequent (auto regen) | No merge conflicts |
| **Git Versioning** | ✅ Commit | ❌ Ignore | Clean repository |
| **Purpose** | Configuration | Runtime metadata | Different lifecycles |
| **Editability** | Human-edited | Machine-generated | Prevents accidental overwrites |

---

## 🔄 Workflow

### config/ - Manual Configuration

**When to edit**:
- Adding new varieties
- Changing project defaults
- Modifying relationships
- Updating templates

**How to edit**:
```bash
vim config/project.yaml
git add config/*.yaml
git commit -m "Update configuration"
```

---

### _meta/ - Auto-generated Metadata

**When to regenerate**:
- New data files added
- Date range extended
- Catalog corrupted
- Fresh start needed

**How to regenerate**:
```bash
python tools/build_catalog_standalone.py
```

**Note**: Files in `_meta/` can be safely deleted - they'll be regenerated on next run.

---

## 📋 File Inventory

### config/ Files (3 files)

#### 1. `project.yaml`
- **Type**: Manual configuration
- **Purpose**: Project-wide settings template
- **Contains**: Paths, calendars, defaults
- **Edit frequency**: Rarely

#### 2. `relations.yaml`
- **Type**: Manual configuration  
- **Purpose**: Cross-variety relationship definitions
- **Contains**: Variety correlations for factors
- **Edit frequency**: When adding varieties

#### 3. `main_contracts.example.yaml`
- **Type**: Example template
- **Purpose**: Shows YAML format for main contracts
- **Contains**: Format reference only
- **Edit frequency**: Never (reference only)

---

### _meta/ Files (3 files)

#### 1. `catalog.parquet`
- **Type**: Auto-generated
- **Size**: ~500KB
- **Records**: 6,752 files
- **Purpose**: Index of all data files
- **Regenerated**: When data changes

#### 2. `main_contracts_auto.yaml`
- **Type**: Auto-generated
- **Size**: ~18KB
- **Records**: 322 trading days
- **Purpose**: Main contract configuration
- **Regenerated**: With catalog

#### 3. `README.md`
- **Type**: Documentation
- **Purpose**: Explains `_meta/` directory purpose
- **Audience**: Developers

---

## 🔍 Usage Examples

### Load Manual Configuration

```python
import yaml

# Load project settings
with open('config/project.yaml') as f:
    project_cfg = yaml.safe_load(f)

# Load relationships
with open('config/relations.yaml') as f:
    relations = yaml.safe_load(f)
```

---

### Load Auto-generated Metadata

```python
import pandas as pd

# Load catalog
catalog = pd.read_parquet('_meta/catalog.parquet')

# Load main contracts
with open('_meta/main_contracts_auto.yaml') as f:
    contracts = yaml.safe_load(f)['contracts']
```

---

### Regenerate Metadata

```bash
# Delete old metadata
rm -rf _meta/

# Generate fresh
python tools/build_catalog_standalone.py
```

---

## 🚫 What NOT to Do

### ❌ Don't Edit Auto-generated Files

```bash
# WRONG
vim _meta/main_contracts_auto.yaml  # Will be overwritten!

# RIGHT - if you need custom contracts
cp _meta/main_contracts_auto.yaml _meta/main_contracts_custom.yaml
# Then modify your custom version
```

---

### ❌ Don't Commit _meta/

```bash
# Check your .gitignore includes:
_meta/
```

**Why**: 
- Large binary files (catalog.parquet)
- Constantly changing
- Can be regenerated
- Pollutes git history

---

### ❌ Don't Put Templates in _meta/

```bash
# WRONG location
config/project.yaml → _meta/project.yaml  # NO!

# RIGHT location stays
config/project.yaml  # ✓
```

---

## 💡 Best Practices

### 1. Document config/ Changes

```yaml
# project.yaml
# v1.2.0 - 2024-03-31
# - Updated cache root path
# - Added new worker configuration
```

---

### 2. Regular Metadata Refresh

```bash
# Weekly refresh
python tools/build_catalog_standalone.py

# Verify
python -c "import pandas as pd; print(len(pd.read_parquet('_meta/catalog.parquet')))"
```

---

### 3. Backup Before Major Changes

```bash
# Backup manual configs
cp -r config/ config_backup_$(date +%Y%m%d)/

# Safe to experiment now
```

---

### 4. Use .gitignore

Already included in `.gitignore`:
```
# Auto-generated metadata
_meta/
_portal_cache/
*_cache/
output_*/
store/
```

---

## ✅ Verification Results

### Test 1: Directory Structure

```bash
$ find config _meta -maxdepth 1 -type f | sort
config/main_contracts.example.yaml
config/project.yaml
config/relations.yaml
_meta/catalog.parquet
_meta/main_contracts_auto.yaml
_meta/README.md
```

✅ **Correct** - All files in proper locations

---

### Test 2: Load Testing

```python
# config/ files
✓ project.yaml loaded
✓ relations.yaml loaded

# _meta/ files
✓ catalog.parquet loaded (6,752 records)
✓ main_contracts_auto.yaml loaded (322 days)
```

✅ **All files load successfully**

---

### Test 3: Production Script

```bash
$ python pipeline/produce_all_factors_unified.py --test --workers 1
Using YAML main contract config: _meta/main_contracts_auto.yaml
[1] Loading Catalog... Loaded 6752 files
[2] Analyzing available data... Symbols: ['fe', 'hc', 'rb']
```

✅ **Production script works correctly**

---

## 📖 Related Documentation

- [`CONFIG_MANAGEMENT_GUIDE.md`](CONFIG_MANAGEMENT_GUIDE.md) - Complete configuration guide
- [`_meta/README.md`](_meta/README.md) - Auto-generated files explanation
- [`YAML_MIGRATION_GUIDE.md`](YAML_MIGRATION_GUIDE.md) - YAML format documentation
- [`.gitignore`](.gitignore) - Git ignore rules

---

## 🎉 Summary

### Before (Unclear Separation)

```
config/
├── project.yaml
└── main_contracts.yaml  # Confusing - manual or auto?

_meta/
└── catalog.parquet
```

**Problems**:
- ❌ Unclear responsibilities
- ❌ Risk of committing auto-generated files
- ❌ No documentation

---

### After (Clear Separation)

```
config/                          # Manual templates
├── project.yaml
├── relations.yaml
└── main_contracts.example.yaml  # ← Renamed (example only)

_meta/                           # Auto-generated
├── catalog.parquet
├── main_contracts_auto.yaml     # ← Auto-generated
└── README.md                    # ← Documentation
```

**Benefits**:
- ✅ Clear responsibility separation
- ✅ Git-friendly (`.gitignore _meta/`)
- ✅ Well documented
- ✅ Prevents accidental overwrites
- ✅ Easy to understand and maintain

---

**Status**: ✅ Complete and Verified  
**Date**: 2025-03-31  
**Files Modified**: 7  
**Tests Passed**: 3/3
