from typing import List, Optional

from pydantic import BaseModel


class ActivityCreate(BaseModel):
    name: str


class ActivityUpdate(BaseModel):
    name: Optional[str] = None


class BulkActivities(BaseModel):
    names: List[str]


class ElementCreate(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    testrl: Optional[str] = None
    teenrl: Optional[str] = None


class ElementUpdate(BaseModel):
    name: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    testrl: Optional[str] = None
    teenrl: Optional[str] = None


class EpochCreate(BaseModel):
    name: str
    epoch_label: Optional[str] = None
    epoch_description: Optional[str] = None


class EpochUpdate(BaseModel):
    name: Optional[str] = None
    epoch_label: Optional[str] = None
    epoch_description: Optional[str] = None


class VisitCreate(BaseModel):
    name: str
    raw_header: Optional[str] = None
    epoch_id: Optional[int] = None


class VisitUpdate(BaseModel):
    name: Optional[str] = None
    raw_header: Optional[str] = None
    epoch_id: Optional[int] = None


class ArmCreate(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    data_origin_type: Optional[str] = None


class ArmUpdate(BaseModel):
    name: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    data_origin_type: Optional[str] = None


class SOACreate(BaseModel):
    name: str
    study_id: Optional[str] = None
    study_label: Optional[str] = None
    study_description: Optional[str] = None


class SOAMetadataUpdate(BaseModel):
    study_id: Optional[str] = None
    study_label: Optional[str] = None
    study_description: Optional[str] = None
