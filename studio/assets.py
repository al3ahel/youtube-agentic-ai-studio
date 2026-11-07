"""Keep each Studio page on one coherent version of its local browser assets."""
import hashlib
from pathlib import Path
import re


def versioned_page(html, static_folder):
    digest = hashlib.sha256(html.encode("utf-8"))
    root = Path(static_folder)
    files = [root / "app.css", *sorted((root / "js").glob("*.js"))]
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    version = digest.hexdigest()[:20]
    return re.sub(r'\b(src|href)="(/static/(?:js/[^"?]+\.js|app\.css))"',
                  lambda match: f'{match[1]}="{match[2]}?v={version}"', html)
