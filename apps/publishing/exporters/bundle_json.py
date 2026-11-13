import json

from .base import Exporter, ExportFile, register


@register
class BundleJSONExporter(Exporter):
    """The canonical Event Bundle — what apps, mobile clients and LLM tools read."""

    slug = "json"
    label = "Event Bundle JSON (canonical)"

    def filenames(self, bundle):
        return {"json": f"{bundle['event']['slug']}.json"}

    def render(self, bundle, context):
        payload = {**bundle, "feeds": {**bundle.get("feeds", {}), **context.feeds}}
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode()
        return [ExportFile(self.filenames(bundle)["json"], body, "application/json")]
