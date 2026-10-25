# SPDX-License-Identifier: Apache-2.0
"""Bounded cached design diagnostics; no solve, geometry validation, or writes."""
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import threading
import unicodedata

METHODS = frozenset(('inspect_design',))
CAPABILITIES = {'version': 1, 'method': 'inspect_design', 'read_only': True,
                'max_response_bytes': 131072, 'constraint_index_base': 0, 'constraint_limit': 100,
                'geometry_freshness': 'not_verified'}
MAX_READ_BYTES = 128 * 1024
MAX_STRING = 512
MAX_ITEMS = 100
_PARAMETERS = ('Length', 'Length2', 'Radius', 'Angle', 'Midplane', 'Reversed', 'Type', 'Profile',
               'Width', 'Height', 'Thickness', 'Offset')
_QUANTITY_TYPES = frozenset(('App::PropertyLength', 'App::PropertyDistance', 'App::PropertyAngle',
                             'App::PropertyQuantity', 'App::PropertyFloatConstraint'))
_NUMBER_TYPES = frozenset(('App::PropertyFloat', 'App::PropertyInteger', 'App::PropertyIntegerConstraint'))
_STRING_TYPES = frozenset(('App::PropertyString', 'App::PropertyEnumeration'))
_LINK_TYPES = frozenset(('App::PropertyLink', 'App::PropertyLinkSub', 'App::PropertyLinkList', 'App::PropertyLinkSubList'))
_KEYS = frozenset(('docName', 'objectName', 'constraintOffset', 'constraintLimit',
                   'expectedDocumentInstanceToken', 'expectedRevision'))


def _name(value):
    return (isinstance(value, str) and 0 < len(value) <= 256
            and not any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') for char in value))


def validate(method, params, Error):
    if not isinstance(method, str) or method not in METHODS:
        return False
    if not isinstance(params, dict) or set(params) - _KEYS:
        raise Error('INVALID_PARAMS', '设计检查参数必须是对象且不能含未开放字段。')
    if 'objectName' not in params or not _name(params['objectName']):
        raise Error('INVALID_PARAMS', 'objectName 必须是明确的对象内部名称。')
    if 'docName' in params and not _name(params['docName']):
        raise Error('INVALID_PARAMS', 'docName 必须是有效的文档内部名称。')
    for key, minimum, maximum in (('constraintOffset', 0, 2147483647), ('constraintLimit', 1, 100)):
        if key in params and (type(params[key]) is not int or not minimum <= params[key] <= maximum):
            raise Error('INVALID_PARAMS', '约束分页偏移必须为非负整数，数量必须为1到100。')
    if 'expectedDocumentInstanceToken' in params:
        token = params['expectedDocumentInstanceToken']
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', token):
            raise Error('INVALID_PARAMS', 'expectedDocumentInstanceToken 必须是读取返回的文档实例令牌。')
    if 'expectedRevision' in params:
        revision = params['expectedRevision']
        if type(revision) is not int or not 0 <= revision <= 9007199254740991 or 'expectedDocumentInstanceToken' not in params:
            raise Error('INVALID_PARAMS', 'expectedRevision 必须是安全非负整数，并同时提供 expectedDocumentInstanceToken。')
    return True


def _operations(Error):
    name = 'pft_operations'
    filename = Path(__file__).resolve().with_name(name + '.py')
    module = sys.modules.get(name)
    if module is not None:
        try:
            if Path(getattr(module, '__file__', None)).resolve() != filename:
                raise ValueError()
        except Exception:
            raise Error('STATE_SOURCE_MISMATCH', '设计读取模块来源冲突，请重新加载一致的插件。') from None
    else:
        spec = importlib.util.spec_from_file_location(name, filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            if sys.modules.get(name) is module:
                sys.modules.pop(name, None)
            raise Error('STATE_SOURCE_MISMATCH', '设计读取模块无法从一致插件来源加载。') from None
    return module


def _read(obj, name):
    try:
        return getattr(obj, name), 'available'
    except AttributeError:
        return None, 'unavailable'
    except Exception:
        return None, 'read_failed'


def _cached(value=None, status='unavailable', source=None):
    return {'value': value if status == 'available' else None, 'status': status,
            'source': source if status == 'available' else None}


def _warn(warnings, text):
    if text not in warnings and len(warnings) < 30:
        warnings.append(text)


def _text(value, operations, warnings, maximum=MAX_STRING, truncate=False):
    if not isinstance(value, str):
        return None
    if (operations._is_private_text(value) or any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') and char not in '\n\t' for char in value)):
        _warn(warnings, '路径、控制字符或私有源码文本未返回。')
        return None
    if len(value) > maximum:
        _warn(warnings, '部分文本超过返回长度限制。')
        return value[:maximum] if truncate else None
    return value


def _integer(value, nonnegative=False):
    return type(value) is int and (0 if nonnegative else -2147483648) <= value <= 2147483647


def _scalar_cached(obj, name, checker, source):
    value, status = _read(obj, name)
    if status == 'available' and not checker(value):
        status = 'unsupported'
    return _cached(value, status, source)


def _reference(value, document, operations, warnings):
    subelements = []
    if isinstance(value, (tuple, list)):
        if len(value) != 2 or not isinstance(value[1], (tuple, list)) or len(value[1]) > 100:
            return None
        value, raw = value
        for subelement in raw:
            text = _text(subelement, operations, warnings)
            if text is None or not text:
                return None
            subelements.append(text)
    owner, status = _read(value, 'Document')
    if status != 'available' or owner is not document:
        return None  # Do not inspect the external object's Name or CAD data.
    name, status = _read(value, 'Name')
    if status != 'available' or not _name(name) or operations._is_private_text(name):
        return None
    try:
        if document.getObject(name) is not value:
            return None
    except Exception:
        return None
    return {'object': name, 'subelements': subelements}


def _quantity(value, operations, warnings):
    if type(value).__name__ != 'Quantity':
        return None
    serializer = operations._PftJson()
    result = serializer.value(value)
    for warning in serializer.warnings:
        _warn(warnings, warning)
    if not isinstance(result, dict) or result.get('kind') != 'quantity':
        return None
    number = result.get('value')
    number = number if type(number) in (int, float) and math.isfinite(number) and abs(number) <= 9007199254740991 else None
    return {'kind': 'quantity', 'value': number, 'unit': result.get('unit') if isinstance(result.get('unit'), str) else None,
            'unit_signature': result.get('unit_signature'), 'unit_system': result.get('unit_system'),
            'unit_type': result.get('unit_type') if isinstance(result.get('unit_type'), str) else None}


def _body(obj, document, operations, warnings):
    tip, tip_status = _read(obj, 'Tip')
    tip_ref = _reference(tip, document, operations, warnings) if tip is not None else None
    if tip_status == 'available' and tip is not None and tip_ref is None:
        tip_status = 'unsupported'
    group, status = _read(obj, 'Group')
    if status == 'available' and not isinstance(group, (list, tuple)):
        status = 'unsupported'
    members, count, truncated = [], None, False
    if status == 'available':
        count = len(group)
        truncated = count > MAX_ITEMS
        for member in group[:MAX_ITEMS]:
            ref = _reference(member, document, operations, warnings)
            if ref is None:
                truncated = True
                _warn(warnings, '部分对象引用不属于当前文档或无法读取，未沿来源展开。')
            else:
                members.append(ref)
    return {'status': status, 'tip': tip_ref, 'tip_status': tip_status, 'members': members,
            'member_count': count, 'members_truncated': truncated}


def _method_cached(obj, method, index, convert, source):
    function, status = _read(obj, method)
    if status != 'available' or not callable(function):
        return _cached(status=status if status != 'available' else 'unsupported')
    try:
        value = convert(function(index))
    except Exception:
        return _cached(status='read_failed')
    return _cached(value, 'available' if value is not None else 'unsupported', source)


def _sketch(obj, params, operations, warnings):
    geometry = _scalar_cached(obj, 'GeometryCount', lambda value: _integer(value, True), 'Sketch.GeometryCount')
    degrees = _scalar_cached(obj, 'DoF', lambda value: _integer(value, True), 'Sketch.DoF')
    if degrees['status'] == 'unavailable':
        degrees = _scalar_cached(obj, 'SolverDOFs', lambda value: _integer(value, True), 'Sketch.SolverDOFs')
    fully = _scalar_cached(obj, 'FullyConstrained', lambda value: type(value) is bool, 'Sketch.FullyConstrained')
    offset, limit = params.get('constraintOffset', 0), params.get('constraintLimit', 50)
    constraints, status = _read(obj, 'Constraints')
    if status == 'available' and not isinstance(constraints, (list, tuple)):
        status = 'unsupported'
    page = {'status': status, 'total_count': None, 'offset': offset, 'limit': limit,
            'items': [], 'has_more': None, 'next_offset': None}
    if status == 'available':
        total = len(constraints)
        page['total_count'] = total
        for index in range(offset, min(total, offset + limit)):
            constraint = constraints[index]
            row = {'index': index, 'status': 'available'}
            for name in ('Type', 'Name', 'First', 'FirstPos', 'Second', 'SecondPos', 'Third', 'ThirdPos'):
                value, field_status = _read(constraint, name)
                value = _text(value, operations, warnings) if name in ('Type', 'Name') else value if _integer(value) else None
                row[name] = value
                if field_status != 'available' or value is None:
                    row['status'] = 'partial'
            row['is_driving'] = _method_cached(obj, 'getDriving', index, lambda value: value if type(value) is bool else None, 'Sketch.getDriving')
            row['is_active'] = _method_cached(obj, 'getActive', index, lambda value: value if type(value) is bool else None, 'Sketch.getActive')
            row['datum'] = _method_cached(obj, 'getDatum', index, lambda value: _quantity(value, operations, warnings), 'Sketch.getDatum')
            page['items'].append(row)
        end = offset + len(page['items'])
        page['has_more'] = end < total
        page['next_offset'] = end if page['has_more'] else None
    if any(value['status'] != 'available' for value in (geometry, degrees, fully)):
        _warn(warnings, '部分草图缓存状态不可用；未调用求解或重算。')
    return {'geometry_count': geometry, 'degrees_of_freedom': degrees, 'fully_constrained': fully,
            'constraints': page, 'solver_invoked': False}


def _parameter_value(raw, property_type, document, operations, warnings):
    if property_type in _QUANTITY_TYPES:
        return _quantity(raw, operations, warnings)
    if property_type == 'App::PropertyBool':
        return {'kind': 'boolean', 'value': raw} if type(raw) is bool else None
    if property_type in _NUMBER_TYPES:
        if type(raw) in (int, float) and math.isfinite(raw) and abs(raw) <= 9007199254740991:
            return {'kind': 'number', 'value': raw}
        return None
    if property_type in _STRING_TYPES:
        text = _text(raw, operations, warnings)
        return {'kind': 'string', 'value': text} if text is not None else None
    if property_type in ('App::PropertyLink', 'App::PropertyLinkSub'):
        ref = _reference(raw, document, operations, warnings)
        return {'kind': 'reference', **ref} if ref is not None else None
    if property_type in ('App::PropertyLinkList', 'App::PropertyLinkSubList') and isinstance(raw, (list, tuple)):
        refs = [_reference(value, document, operations, warnings) for value in raw[:MAX_ITEMS]]
        if any(ref is None for ref in refs):
            return None
        return {'kind': 'references', 'items': refs, 'truncated': len(raw) > MAX_ITEMS}
    return None


def _parameters(obj, document, operations, warnings):
    properties, status = _read(obj, 'PropertiesList')
    if status == 'available' and (not isinstance(properties, (list, tuple)) or any(not isinstance(name, str) for name in properties)):
        status = 'unsupported'
    if status != 'available':
        return [], status, None
    result = []
    for name in _PARAMETERS:
        if name not in properties:
            continue
        row = {'name': name, 'property_type': None, 'status': 'unavailable', 'value': None}
        getter, status = _read(obj, 'getTypeIdOfProperty')
        if status == 'available' and callable(getter):
            try:
                property_type = getter(name)
                if property_type in _QUANTITY_TYPES | _NUMBER_TYPES | _STRING_TYPES | _LINK_TYPES | {'App::PropertyBool'}:
                    row['property_type'] = property_type
                    raw, status = _read(obj, name)
                    if status == 'available':
                        row['value'] = _parameter_value(raw, property_type, document, operations, warnings)
                        status = 'available' if row['value'] is not None else 'unsupported'
                    row['status'] = status
                else:
                    row['status'] = 'unsupported'
            except Exception:
                row['status'] = 'read_failed'
        else:
            row['status'] = status if status != 'available' else 'unsupported'
        result.append(row)
    return result, 'available', 'Object.PropertiesList'


def _expression_text(value, warnings):
    """Accept a small arithmetic grammar, never strings, paths, calls or code.

    A slash is accepted only as a binary operator with a numeric/parenthesized
    denominator. This deliberately leaves ambiguous bare relative paths and
    unsupported expression-language constructs unknown rather than echoing them.
    """
    def rejected():
        _warn(warnings, '部分表达式不在保守算术语法范围内，未返回原文。')
        return None
    if (not isinstance(value, str) or not value or len(value) > MAX_STRING
            or any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') for char in value)):
        return rejected()
    # File suffixes remain private even when their spelling resembles a property.
    if re.search(r'\.(?:fcstd\d*|fcbak|step|stp|iges|igs|brep|brp|stl|obj|3mf|amf|svg|dxf|fcmacro|py|pyc|json|toml|xml|txt|png|jpe?g|pdf)(?:\b|$)', value, re.I):
        return rejected()
    number = r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?'
    identifier = r'[^\W\d]\w*(?:\.[^\W\d]\w*)*'
    token = re.compile(r'\s*(%s|%s|\*\*|[+*/^%%()\-])' % (number, identifier), re.UNICODE)
    tokens, offset = [], 0
    while offset < len(value):
        match = token.match(value, offset)
        if match is None:
            if value[offset:].isspace():
                break
            return rejected()
        tokens.append(match.group(1)); offset = match.end()
    units = frozenset(('mm', 'cm', 'm', 'um', 'nm', 'in', 'ft', 'deg', 'rad', 's', 'kg', 'g', 'N', 'Pa', 'kPa', 'MPa'))
    numeric = re.compile(number + r'\Z')
    position = [0]
    def valid_reference(text):
        parts = text.split('.')
        if (not all(part.isidentifier() and not part.startswith('_') for part in parts)
                or parts[0].lower() in ('os', 'sys', 'subprocess', 'builtins', 'app', 'gui', 'freecad', 'freecadgui', 'http', 'https', 'ftp', 'file', 'www')):
            return False
        if len(parts) > 1:
            return (parts[-1] in _PARAMETERS or (len(parts) == 2 and parts[0] == 'Constraints')
                    or re.fullmatch(r'[A-Z]+[1-9][0-9]{0,5}', parts[-1]) is not None)
        return text in _PARAMETERS or text in ('pi', 'e', 'true', 'false')
    def atom():
        if position[0] >= len(tokens):
            return False
        text = tokens[position[0]]
        if text in ('+', '-'):
            position[0] += 1
            return atom()
        if text == '(':
            position[0] += 1
            if not expression() or position[0] >= len(tokens) or tokens[position[0]] != ')':
                return False
            position[0] += 1
            return True
        position[0] += 1
        if numeric.fullmatch(text):
            if not math.isfinite(float(text)):
                return False
            if position[0] < len(tokens) and tokens[position[0]] in units:
                position[0] += 1
            return True
        return valid_reference(text)
    def product():
        if not atom():
            return False
        while position[0] < len(tokens) and tokens[position[0]] in ('*', '/', '^', '**', '%'):
            operator = tokens[position[0]]; position[0] += 1
            if operator == '/':
                following = position[0]
                while following < len(tokens) and tokens[following] in ('+', '-'):
                    following += 1
                if following >= len(tokens) or not (tokens[following] == '(' or numeric.fullmatch(tokens[following])):
                    return False
            if not atom():
                return False
        return True
    def expression():
        if not product():
            return False
        while position[0] < len(tokens) and tokens[position[0]] in ('+', '-'):
            position[0] += 1
            if not product():
                return False
        return True
    try:
        return value if expression() and position[0] == len(tokens) else rejected()
    except (ValueError, OverflowError, RecursionError):
        return rejected()


def _expressions(obj, operations, warnings):
    expressions, status = _read(obj, 'ExpressionEngine')
    if status == 'available' and not isinstance(expressions, (list, tuple)):
        status = 'unsupported'
    result = {'status': status, 'items': [], 'total_count': None, 'truncated': False, 'evaluated': False}
    if status == 'available':
        result['total_count'] = len(expressions)
        result['truncated'] = len(expressions) > 50
        for entry in expressions[:50]:
            path = expression = None
            if isinstance(entry, (tuple, list)) and len(entry) == 2:
                path = _text(entry[0], operations, warnings)
                expression = _expression_text(entry[1], warnings)
            valid = path is not None and expression is not None
            result['items'].append({'path': path if valid else None, 'expression': expression if valid else None,
                                    'status': 'available' if valid else 'unsupported'})
    return result


def perform(method, params, App, Gui, Error):
    if not validate(method, params, Error):
        raise Error('METHOD_NOT_FOUND', '本设计检查模块没有开放该方法。')
    if threading.current_thread() is not threading.main_thread():
        raise Error('MAIN_THREAD_REQUIRED', '设计检查必须在 FreeCAD GUI 主线程执行。')
    operations = _operations(Error)
    try:
        state_helper = operations._document_state_helper()
    except operations.PftDocumentStateSourceMismatch:
        raise Error('STATE_SOURCE_MISMATCH', '文档状态模块来源冲突，请重新加载一致的插件。') from None
    document = operations._document(App, params, Error)
    before = state_helper.snapshot(document, App)
    state_helper.check_expected(before, params, Error)
    obj = operations._object(document, params['objectName'], Error)
    if _read(obj, 'Document')[0] is not document or _read(obj, 'Name')[0] != params['objectName']:
        raise Error('OBJECT_NOT_FOUND', '指定对象不存在；请使用内部 Name。')
    warnings = ['仅报告当前原生缓存与部分事件修订；未求解、重算或验证几何有效性。']
    kind_value, _ = _read(obj, 'TypeId')
    kind = 'body' if kind_value == 'PartDesign::Body' else 'sketch' if kind_value in ('Sketcher::SketchObject', 'Sketcher::SketchObjectPython') else 'unsupported' if isinstance(kind_value, str) and kind_value.startswith('App::Link') else 'feature'
    label = _text(_read(obj, 'Label')[0], operations, warnings, truncate=True)
    type_id = _text(kind_value, operations, warnings, maximum=256)
    states, states_status = _read(obj, 'State')
    state_values, error_marker = None, None
    if states_status == 'available':
        if not isinstance(states, (list, tuple)):
            states_status = 'unsupported'
        else:
            state_values = [_text(value, operations, warnings) for value in states[:100]]
            complete = len(states) <= 100 and all(value is not None for value in state_values)
            state_values = [value for value in state_values if value is not None]
            found = any(any(marker in value.lower() for marker in ('error', 'invalid', 'failed')) for value in state_values)
            error_marker = True if found else False if complete else None
            if not complete:
                _warn(warnings, '部分对象状态标记未返回，不能据此证明对象有效。')
    if type_id is None or kind == 'feature' and not type_id.startswith(('Part::', 'PartDesign::')):
        kind = 'unsupported'
    unsupported = kind == 'unsupported'
    parameter_rows, parameters_status, parameters_source = ([], 'unsupported', None) if unsupported else _parameters(obj, document, operations, warnings)
    result = {'document': document.Name, 'source_kind': 'live_freecad_gui', **before,
              'object': {'Name': params['objectName'], 'Label': label, 'TypeId': type_id, 'kind': kind,
                         'status': 'unsupported' if unsupported else 'available',
                         'reason': ('link_source_not_inspected' if isinstance(kind_value, str) and kind_value.startswith('App::Link') else 'unsupported_object_type') if unsupported else None,
                         'state': _cached(state_values, states_status, 'Object.State'), 'has_error_marker': error_marker},
              'body': _body(obj, document, operations, warnings) if kind == 'body' else None,
              'sketch': _sketch(obj, params, operations, warnings) if kind == 'sketch' else None,
              'parameters': parameter_rows, 'parameters_status': parameters_status, 'parameters_source': parameters_source,
              'expressions': {'status': 'unsupported', 'items': [], 'total_count': None, 'truncated': False, 'evaluated': False} if unsupported else _expressions(obj, operations, warnings),
              'solver_invoked': False, 'recomputed': False, 'geometry_validity': 'not_verified', 'warnings': warnings}
    state_helper.check_unchanged(before, document, App, Error)
    if document.getObject(params['objectName']) is not obj:
        raise Error('OBJECT_INSTANCE_MISMATCH', '读取期间对象已替换，请重新读取。')
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_READ_BYTES:
        raise Error('READ_RESPONSE_TOO_LARGE', '读取结果超过128 KiB预算，请缩小读取范围。')
    return result
