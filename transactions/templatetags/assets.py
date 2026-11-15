"""{% asset "transactions/style.css" %} -> /static/transactions/style.css?v=1763222887

The ?v= number is the file's last-modified time. When the file changes, the URL
changes, so browsers download the new version instead of using an old cached copy.
"""

from pathlib import Path

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def asset(path):
    url = static(path)
    found = finders.find(path)
    if not found:
        return url
    version = int(Path(found).stat().st_mtime)
    return f"{url}?v={version}"
