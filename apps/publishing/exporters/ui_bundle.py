"""Interactive HTML/JS schedule: ship a prebuilt conference-schedule-ui.

Set settings.EVENT_PLANNER["SCHEDULE_UI_DIST_DIR"] to a built `dist/`
directory of conference-schedule-ui. Its files are published next to the
feeds, with `window.DATA_URL` (index.html) and `DATA_URL` (sw.js) pointed at
this event's schedule_ui JSON. Disabled (no files) when the setting is unset.

Phase-2 note: replace with a UI that reads the Event Bundle directly.
"""

import mimetypes
import re
from pathlib import Path

from django.conf import settings

from .base import Exporter, ExportFile, register

_INDEX_RE = re.compile(r"window\.DATA_URL\s*=\s*'[^']*'")
_SW_RE = re.compile(r"const DATA_URL = \"[^\"]*\"")


@register
class UIBundleExporter(Exporter):
    slug = "ui"
    label = "Interactive schedule (HTML/JS)"

    @staticmethod
    def dist_dir() -> Path | None:
        path = settings.EVENT_PLANNER.get("SCHEDULE_UI_DIST_DIR")
        return Path(path) if path and Path(path, "index.html").exists() else None

    @property
    def default(self):  # only publish by default when a build is configured
        return self.dist_dir() is not None

    def filenames(self, bundle):
        return {"ui": "index.html"} if self.dist_dir() else {}

    def render(self, bundle, context):
        dist = self.dist_dir()
        if dist is None:
            return []
        data_url = context.feeds.get("schedule_ui", "")
        files = []
        for path in sorted(p for p in dist.rglob("*") if p.is_file()):
            rel = path.relative_to(dist).as_posix()
            content = path.read_bytes()
            if rel == "index.html":
                content = _INDEX_RE.sub(f"window.DATA_URL='{data_url}'", content.decode()).encode()
            elif rel == "sw.js":
                content = _SW_RE.sub(f'const DATA_URL = "{data_url}"', content.decode()).encode()
            ctype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
            files.append(ExportFile(rel, content, ctype))
        return files
