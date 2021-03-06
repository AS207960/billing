# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: geoip.proto
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()


from google.protobuf import wrappers_pb2 as google_dot_protobuf_dot_wrappers__pb2


DESCRIPTOR = _descriptor.FileDescriptor(
  name='geoip.proto',
  package='geoip',
  syntax='proto3',
  serialized_options=None,
  create_key=_descriptor._internal_create_key,
  serialized_pb=b'\n\x0bgeoip.proto\x12\x05geoip\x1a\x1egoogle/protobuf/wrappers.proto\"F\n\x0cGeoIPRequest\x12+\n\tip_lookup\x18\x01 \x01(\x0b\x32\x16.geoip.IPLookupRequestH\x00\x42\t\n\x07message\"F\n\x0fIPLookupRequest\x12\x13\n\tipv4_addr\x18\x01 \x01(\x07H\x00\x12\x13\n\tipv6_addr\x18\x02 \x01(\x0cH\x00\x42\t\n\x07ip_addr\"\x81\x04\n\x10IPLookupResponse\x12\x36\n\x06status\x18\x01 \x01(\x0e\x32&.geoip.IPLookupResponse.IPLookupStatus\x12\x32\n\x04\x64\x61ta\x18\x02 \x01(\x0b\x32$.geoip.IPLookupResponse.IPLookupData\x1a\xca\x02\n\x0cIPLookupData\x12-\n\x07\x63ountry\x18\x01 \x01(\x0b\x32\x1c.google.protobuf.StringValue\x12\x31\n\x0bpostal_code\x18\x02 \x01(\x0b\x32\x1c.google.protobuf.StringValue\x12/\n\ttime_zone\x18\x03 \x01(\x0b\x32\x1c.google.protobuf.StringValue\x12\x30\n\nmetro_code\x18\x04 \x01(\x0b\x32\x1c.google.protobuf.UInt32Value\x12.\n\x08latitude\x18\x05 \x01(\x0b\x32\x1c.google.protobuf.DoubleValue\x12/\n\tlongitude\x18\x06 \x01(\x0b\x32\x1c.google.protobuf.DoubleValue\x12\x14\n\x0csubdivisions\x18\x07 \x03(\t\"4\n\x0eIPLookupStatus\x12\x0b\n\x07UNKNOWN\x10\x00\x12\x06\n\x02OK\x10\x01\x12\r\n\tNOT_FOUND\x10\x02\x62\x06proto3'
  ,
  dependencies=[google_dot_protobuf_dot_wrappers__pb2.DESCRIPTOR,])



_IPLOOKUPRESPONSE_IPLOOKUPSTATUS = _descriptor.EnumDescriptor(
  name='IPLookupStatus',
  full_name='geoip.IPLookupResponse.IPLookupStatus',
  filename=None,
  file=DESCRIPTOR,
  create_key=_descriptor._internal_create_key,
  values=[
    _descriptor.EnumValueDescriptor(
      name='UNKNOWN', index=0, number=0,
      serialized_options=None,
      type=None,
      create_key=_descriptor._internal_create_key),
    _descriptor.EnumValueDescriptor(
      name='OK', index=1, number=1,
      serialized_options=None,
      type=None,
      create_key=_descriptor._internal_create_key),
    _descriptor.EnumValueDescriptor(
      name='NOT_FOUND', index=2, number=2,
      serialized_options=None,
      type=None,
      create_key=_descriptor._internal_create_key),
  ],
  containing_type=None,
  serialized_options=None,
  serialized_start=660,
  serialized_end=712,
)
_sym_db.RegisterEnumDescriptor(_IPLOOKUPRESPONSE_IPLOOKUPSTATUS)


_GEOIPREQUEST = _descriptor.Descriptor(
  name='GeoIPRequest',
  full_name='geoip.GeoIPRequest',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  create_key=_descriptor._internal_create_key,
  fields=[
    _descriptor.FieldDescriptor(
      name='ip_lookup', full_name='geoip.GeoIPRequest.ip_lookup', index=0,
      number=1, type=11, cpp_type=10, label=1,
      has_default_value=False, default_value=None,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto3',
  extension_ranges=[],
  oneofs=[
    _descriptor.OneofDescriptor(
      name='message', full_name='geoip.GeoIPRequest.message',
      index=0, containing_type=None,
      create_key=_descriptor._internal_create_key,
    fields=[]),
  ],
  serialized_start=54,
  serialized_end=124,
)


_IPLOOKUPREQUEST = _descriptor.Descriptor(
  name='IPLookupRequest',
  full_name='geoip.IPLookupRequest',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  create_key=_descriptor._internal_create_key,
  fields=[
    _descriptor.FieldDescriptor(
      name='ipv4_addr', full_name='geoip.IPLookupRequest.ipv4_addr', index=0,
      number=1, type=7, cpp_type=3, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='ipv6_addr', full_name='geoip.IPLookupRequest.ipv6_addr', index=1,
      number=2, type=12, cpp_type=9, label=1,
      has_default_value=False, default_value=b"",
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto3',
  extension_ranges=[],
  oneofs=[
    _descriptor.OneofDescriptor(
      name='ip_addr', full_name='geoip.IPLookupRequest.ip_addr',
      index=0, containing_type=None,
      create_key=_descriptor._internal_create_key,
    fields=[]),
  ],
  serialized_start=126,
  serialized_end=196,
)


_IPLOOKUPRESPONSE_IPLOOKUPDATA = _descriptor.Descriptor(
  name='IPLookupData',
  full_name='geoip.IPLookupResponse.IPLookupData',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  create_key=_descriptor._internal_create_key,
  fields=[
    _descriptor.FieldDescriptor(
      name='country', full_name='geoip.IPLookupResponse.IPLookupData.country', index=0,
      number=1, type=11, cpp_type=10, label=1,
      has_default_value=False, default_value=None,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='postal_code', full_name='geoip.IPLookupResponse.IPLookupData.postal_code', index=1,
      number=2, type=11, cpp_type=10, label=1,
      has_default_value=False, default_value=None,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='time_zone', full_name='geoip.IPLookupResponse.IPLookupData.time_zone', index=2,
      number=3, type=11, cpp_type=10, label=1,
      has_default_value=False, default_value=None,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='metro_code', full_name='geoip.IPLookupResponse.IPLookupData.metro_code', index=3,
      number=4, type=11, cpp_type=10, label=1,
      has_default_value=False, default_value=None,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='latitude', full_name='geoip.IPLookupResponse.IPLookupData.latitude', index=4,
      number=5, type=11, cpp_type=10, label=1,
      has_default_value=False, default_value=None,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='longitude', full_name='geoip.IPLookupResponse.IPLookupData.longitude', index=5,
      number=6, type=11, cpp_type=10, label=1,
      has_default_value=False, default_value=None,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='subdivisions', full_name='geoip.IPLookupResponse.IPLookupData.subdivisions', index=6,
      number=7, type=9, cpp_type=9, label=3,
      has_default_value=False, default_value=[],
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto3',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=328,
  serialized_end=658,
)

_IPLOOKUPRESPONSE = _descriptor.Descriptor(
  name='IPLookupResponse',
  full_name='geoip.IPLookupResponse',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  create_key=_descriptor._internal_create_key,
  fields=[
    _descriptor.FieldDescriptor(
      name='status', full_name='geoip.IPLookupResponse.status', index=0,
      number=1, type=14, cpp_type=8, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='data', full_name='geoip.IPLookupResponse.data', index=1,
      number=2, type=11, cpp_type=10, label=1,
      has_default_value=False, default_value=None,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
  ],
  extensions=[
  ],
  nested_types=[_IPLOOKUPRESPONSE_IPLOOKUPDATA, ],
  enum_types=[
    _IPLOOKUPRESPONSE_IPLOOKUPSTATUS,
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto3',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=199,
  serialized_end=712,
)

_GEOIPREQUEST.fields_by_name['ip_lookup'].message_type = _IPLOOKUPREQUEST
_GEOIPREQUEST.oneofs_by_name['message'].fields.append(
  _GEOIPREQUEST.fields_by_name['ip_lookup'])
_GEOIPREQUEST.fields_by_name['ip_lookup'].containing_oneof = _GEOIPREQUEST.oneofs_by_name['message']
_IPLOOKUPREQUEST.oneofs_by_name['ip_addr'].fields.append(
  _IPLOOKUPREQUEST.fields_by_name['ipv4_addr'])
_IPLOOKUPREQUEST.fields_by_name['ipv4_addr'].containing_oneof = _IPLOOKUPREQUEST.oneofs_by_name['ip_addr']
_IPLOOKUPREQUEST.oneofs_by_name['ip_addr'].fields.append(
  _IPLOOKUPREQUEST.fields_by_name['ipv6_addr'])
_IPLOOKUPREQUEST.fields_by_name['ipv6_addr'].containing_oneof = _IPLOOKUPREQUEST.oneofs_by_name['ip_addr']
_IPLOOKUPRESPONSE_IPLOOKUPDATA.fields_by_name['country'].message_type = google_dot_protobuf_dot_wrappers__pb2._STRINGVALUE
_IPLOOKUPRESPONSE_IPLOOKUPDATA.fields_by_name['postal_code'].message_type = google_dot_protobuf_dot_wrappers__pb2._STRINGVALUE
_IPLOOKUPRESPONSE_IPLOOKUPDATA.fields_by_name['time_zone'].message_type = google_dot_protobuf_dot_wrappers__pb2._STRINGVALUE
_IPLOOKUPRESPONSE_IPLOOKUPDATA.fields_by_name['metro_code'].message_type = google_dot_protobuf_dot_wrappers__pb2._UINT32VALUE
_IPLOOKUPRESPONSE_IPLOOKUPDATA.fields_by_name['latitude'].message_type = google_dot_protobuf_dot_wrappers__pb2._DOUBLEVALUE
_IPLOOKUPRESPONSE_IPLOOKUPDATA.fields_by_name['longitude'].message_type = google_dot_protobuf_dot_wrappers__pb2._DOUBLEVALUE
_IPLOOKUPRESPONSE_IPLOOKUPDATA.containing_type = _IPLOOKUPRESPONSE
_IPLOOKUPRESPONSE.fields_by_name['status'].enum_type = _IPLOOKUPRESPONSE_IPLOOKUPSTATUS
_IPLOOKUPRESPONSE.fields_by_name['data'].message_type = _IPLOOKUPRESPONSE_IPLOOKUPDATA
_IPLOOKUPRESPONSE_IPLOOKUPSTATUS.containing_type = _IPLOOKUPRESPONSE
DESCRIPTOR.message_types_by_name['GeoIPRequest'] = _GEOIPREQUEST
DESCRIPTOR.message_types_by_name['IPLookupRequest'] = _IPLOOKUPREQUEST
DESCRIPTOR.message_types_by_name['IPLookupResponse'] = _IPLOOKUPRESPONSE
_sym_db.RegisterFileDescriptor(DESCRIPTOR)

GeoIPRequest = _reflection.GeneratedProtocolMessageType('GeoIPRequest', (_message.Message,), {
  'DESCRIPTOR' : _GEOIPREQUEST,
  '__module__' : 'geoip_pb2'
  # @@protoc_insertion_point(class_scope:geoip.GeoIPRequest)
  })
_sym_db.RegisterMessage(GeoIPRequest)

IPLookupRequest = _reflection.GeneratedProtocolMessageType('IPLookupRequest', (_message.Message,), {
  'DESCRIPTOR' : _IPLOOKUPREQUEST,
  '__module__' : 'geoip_pb2'
  # @@protoc_insertion_point(class_scope:geoip.IPLookupRequest)
  })
_sym_db.RegisterMessage(IPLookupRequest)

IPLookupResponse = _reflection.GeneratedProtocolMessageType('IPLookupResponse', (_message.Message,), {

  'IPLookupData' : _reflection.GeneratedProtocolMessageType('IPLookupData', (_message.Message,), {
    'DESCRIPTOR' : _IPLOOKUPRESPONSE_IPLOOKUPDATA,
    '__module__' : 'geoip_pb2'
    # @@protoc_insertion_point(class_scope:geoip.IPLookupResponse.IPLookupData)
    })
  ,
  'DESCRIPTOR' : _IPLOOKUPRESPONSE,
  '__module__' : 'geoip_pb2'
  # @@protoc_insertion_point(class_scope:geoip.IPLookupResponse)
  })
_sym_db.RegisterMessage(IPLookupResponse)
_sym_db.RegisterMessage(IPLookupResponse.IPLookupData)


# @@protoc_insertion_point(module_scope)
