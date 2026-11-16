from django import template
from apps.control_panel.permissions import route_allowed
from common.record_references import record_label, record_reference
register = template.Library()
register.filter("record_reference", record_reference)
register.filter("record_label", record_label)

@register.simple_tag(takes_context=True)
def panel_can(context, route):
    return route_allowed(context['request'].user, route.removeprefix('panel:'))
