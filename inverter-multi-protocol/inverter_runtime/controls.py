"""Only mapped setters with a matching readback field become HA controls."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re


def checked_number(value, limits):
    try:
        n, lo, hi, step = (Decimal(str(v)) for v in
                           (value, limits['min'], limits['max'], limits['step']))
    except (InvalidOperation, KeyError):
        raise ValueError('Invalid numeric value/bounds') from None
    if not all(v.is_finite() for v in (n, lo, hi, step)) or step <= 0 or not lo <= n <= hi:
        raise ValueError('Value outside allowed bounds')
    if (n-lo) % step:
        raise ValueError('Value does not match the allowed step')
    return float(n)


@dataclass
class Control:
    key: str
    field: str
    query: str
    setter: str
    options: dict = None
    limits: dict = None
    unit: str = ''
    numeric_options: bool = False
    capability_query: str = ''

    def command(self, payload, protocol):
        if self.options is not None:
            if payload not in self.options:
                raise ValueError('Unknown control option')
            command = self.setter + self.options[payload]
        else:
            number = checked_number(payload, self.limits)
            command = self.setter + f'{number:.1f}'
        protocol.definition(command, writing=True)
        return command

    def matches(self, payload, values):
        actual = values[self.field][0]
        if self.numeric_options:
            return Decimal(str(actual)) == Decimal(payload.removesuffix(' A'))
        if self.options is not None:
            return str(actual) == payload
        number = checked_number(payload, self.limits)
        return abs(float(actual)-number) <= self.limits['step']/100

    def state_value(self, value):
        if self.numeric_options:
            return f'{int(value)} A'
        return value


# These mappings reference the decoded setting, not a guessed register value.
SELECTS = {'output_source_priority': ('Output Source Priority', 'POP'),
           'charger_source_priority': ('Charger Source Priority', 'PCP'),
           'input_voltage_range': ('Input Voltage Range', 'PGR'),
           'battery_type': ('Battery Type', 'PBT')}
NUMBERS = {'battery_bulk_charge_voltage': ('Battery Bulk Charge Voltage', 'PCVV'),
           'battery_float_charge_voltage': ('Battery Float Charge Voltage', 'PBFT'),
           'battery_recharge_voltage': ('Battery Recharge Voltage', 'PBCV'),
           'battery_redischarge_voltage': ('Battery Redischarge Voltage', 'PBDV'),
           'battery_cutoff_voltage': ('Battery Under Voltage', 'PSDV')}

CURRENTS = {'max_charging_current': ('Max Charging Current', 'QMCHGCR', 'MCHGC'),
            'max_ac_charging_current': ('Max AC Charging Current', 'QMUCHGCR', 'MUCHGC')}


def controls_for(protocol, settings, enabled, limits, capabilities=None, command_unit=None, charge_current_command="MCHGC"):
    if not protocol.name.startswith('PI30') and protocol.name != 'PI41':
        return {}  # variants require documented mappings before enabling writes
    definition = protocol.codec.COMMANDS.get(protocol.rating, {})
    fields = {d[1]: d for d in definition.get('response', []) if len(d) >= 3}
    controls = {}
    capabilities = capabilities or {}
    for key in enabled:
        if key in SELECTS:
            field, setter = SELECTS[key]
            spec = fields.get(field)
            if field not in settings or not spec or spec[0] != 'option':
                continue
            options = {}
            for index, label in enumerate(spec[2]):
                for suffix in (str(index), f'{index:02d}', f'{index:03d}'):
                    try:
                        protocol.definition(setter+suffix, writing=True)
                        options[label] = suffix
                        break
                    except ValueError:
                        continue
            if options:
                controls[key] = Control(key, field, protocol.rating, setter, options=options)
        elif key in CURRENTS:
            field, inquiry, setter = CURRENTS[key]
            if field not in settings or inquiry not in capabilities:
                continue
            unit = command_unit
            if unit is None and settings.get('Output Mode', ('',))[0] == 'single machine output':
                unit = 0
            if not isinstance(unit, int) or not 0 <= unit <= 9:
                continue  # Never guess a parallel machine's destination.
            options = {}
            for value, _ in capabilities[inquiry].values():
                for token in str(value).split():
                    if not re.fullmatch(r'[0-9]{1,3}', token):
                        continue
                    number = int(token)
                    chosen = charge_current_command if key == 'max_charging_current' else setter
                    digits = 3 if chosen == 'MNCHGC' else 2
                    if number >= 10**digits:
                        continue
                    if key in limits:
                        try:
                            checked_number(number, limits[key])
                        except ValueError:
                            continue
                    suffix = f'{unit}{number:0{digits}d}'
                    try:
                        protocol.definition(chosen+suffix, writing=True)
                    except ValueError:
                        continue
                    options[f'{number} A'] = suffix
            if options:
                controls[key] = Control(key, field, protocol.rating,
                                        charge_current_command if key == 'max_charging_current' else setter, options=options,
                                        unit='A', numeric_options=True, capability_query=inquiry)
        elif key in NUMBERS and key in limits:
            field, setter = NUMBERS[key]
            if field not in settings:
                continue
            bounds = limits[key]
            checked_number(bounds['min'], bounds)
            checked_number(bounds['max'], bounds)
            protocol.definition(setter+f"{bounds['min']:.1f}", writing=True)
            protocol.definition(setter+f"{bounds['max']:.1f}", writing=True)
            if any(Decimal(str(bounds[k])) % Decimal('.1') for k in ('min', 'max', 'step')) or bounds['step'] < .1:
                raise ValueError('PI voltage commands have 0.1 V resolution')
            controls[key] = Control(key, field, protocol.rating, setter, limits=bounds, unit='V')
    return controls
