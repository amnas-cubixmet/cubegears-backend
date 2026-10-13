from django import template
from apps.control_panel.permissions import route_allowed
register = template.Library()

@register.simple_tag(takes_context=True)
def panel_can(context, route):
    return route_allowed(context['request'].user, route.removeprefix('panel:'))
