# -*- coding: utf-8 -*-
"""
File: emsomat/sparkplug/vendor/metadata.py

Vendored from pysparkplug._metadata (Apache 2.0), see NOTICE.md.
"""

import dataclasses
from typing import Optional

from .protobuf_types import MetaData as PB_MetaData
from .types import Self


@dataclasses.dataclass(frozen=True)
class Metadata:
    """Class representing a Sparkplug B metric's metadata"""

    is_multipart: Optional[bool] = None
    content_type: Optional[str] = None
    size: Optional[int] = None
    seq: Optional[int] = None
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    md5: Optional[str] = None
    description: Optional[str] = None

    def to_pb(self) -> PB_MetaData:
        metadata = PB_MetaData()
        if self.is_multipart is not None:
            metadata.is_multi_part = self.is_multipart
        if self.content_type is not None:
            metadata.content_type = self.content_type
        if self.size is not None:
            metadata.size = self.size
        if self.seq is not None:
            metadata.seq = self.seq
        if self.file_name is not None:
            metadata.file_name = self.file_name
        if self.file_type is not None:
            metadata.file_type = self.file_type
        if self.md5 is not None:
            metadata.md5 = self.md5
        if self.description is not None:
            metadata.description = self.description
        return metadata

    @classmethod
    def from_pb(cls, metadata: PB_MetaData) -> Self:
        return cls(
            is_multipart=metadata.is_multi_part
            if metadata.HasField("is_multi_part")
            else None,
            content_type=metadata.content_type
            if metadata.HasField("content_type")
            else None,
            size=metadata.size if metadata.HasField("size") else None,
            seq=metadata.seq if metadata.HasField("seq") else None,
            file_name=metadata.file_name if metadata.HasField("file_name") else None,
            file_type=metadata.file_type if metadata.HasField("file_type") else None,
            md5=metadata.md5 if metadata.HasField("md5") else None,
            description=metadata.description
            if metadata.HasField("description")
            else None,
        )
