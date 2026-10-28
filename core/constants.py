"""Application-wide constants for ScieFig."""

SCHEMA_VERSION = "2.3.0"
SOFTWARE_VERSION = "1.2.2"
APP_NAME = "ScieFig"

WB_STEP_IMPORT = 0
WB_STEP_CROP = 1
WB_STEP_LABELS = 2
WB_STEP_DENSITY = 3
WB_STEP_STATS = 4
WB_STEP_LAYOUT = 5
WB_STEP_EXPORT = 6

SFIG_REQUIRED_DIRS = (
    "schema/",
    "schema/migrations/",
    "logs/",
    "logs/panel_audit/",
    "data/",
    "data/qpcr/",
    "data/wb/",
    "data/fluorescence/",
    "data/external/",
    "panels/",
    "panels/previews/",
    "panels/export_sources/",
    "composer/",
    "thumbnails/",
    "style/",
    "style/user_templates/",
    "exports/",
    "exports/recent/",
    "exports/source_data/",
)

SFIG_REQUIRED_FILES = (
    "manifest.json",
    "project.json",
    "schema/schema_version.json",
    "schema/panel_schema_2.3.0.json",
    "schema/project_schema_2.3.0.json",
    "schema/migrations/index.json",
    "logs/project_audit.log",
    "logs/export_history.log",
    "style/active_template.json",
)

# AI Pack v1 constants
AI_PACK_SUBDIR = "ai_pack"
AI_PACK_MODELS_SUBDIR = "models"
AI_PACK_MANIFEST_VERSION = "1.0"
AI_PACK_DEFAULT_COMMERCIAL_USE_STATUS = "restricted"
AI_PACK_ALLOWED_COMMERCIAL_USE_STATUSES = ("allowed", "restricted", "unknown")
