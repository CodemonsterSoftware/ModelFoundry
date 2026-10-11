from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def url_with(context, **changes):
    """Return '?<query>' built from the current GET params with `changes` applied.

    A change whose value is '' or None removes that key.
    """
    params = context['request'].GET.copy()
    for key, value in changes.items():
        if value is None or value == '':
            params.pop(key, None)
        else:
            params[key] = value
    return '?' + params.urlencode()
