# -*- coding: utf-8 -*-
"""
File: emsomat/sparkplug/vendor/protobuf_types.py

Vendored from pysparkplug._protobuf.__init__ (Apache 2.0), see NOTICE.md.
Pulls the relevant types out of the generated protobuf module.
"""

from . import sparkplug_b_pb2

Payload = sparkplug_b_pb2.Payload
DataSet = Payload.DataSet
DataSetValue = DataSet.DataSetValue
DataSetValueExtension = DataSetValue.DataSetValueExtension
Row = DataSet.Row
MetaData = Payload.MetaData
Metric = Payload.Metric
MetricValueExtension = Metric.MetricValueExtension
PropertySet = Payload.PropertySet
PropertySetList = Payload.PropertySetList
PropertyValue = Payload.PropertyValue
PropertyValueExtension = PropertyValue.PropertyValueExtension
Template = Payload.Template
Parameter = Template.Parameter
ParameterValueExtension = Parameter.ParameterValueExtension
DataType = sparkplug_b_pb2.DataType
