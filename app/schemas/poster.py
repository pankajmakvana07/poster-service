from typing import Any, List, Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class PhotoAreaDef(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    fieldkey: str = Field(default="photo_main", validation_alias=AliasChoices("fieldkey", "fieldKey", "field_key", "key"))
    fieldvalue: Optional[str] = Field(default=None, validation_alias=AliasChoices("fieldvalue", "fieldValue", "field_value", "value", "photoBase64", "photo_base64", "photo"))
    x: float = Field(default=0.0)
    y: float = Field(default=0.0)
    width: float = Field(default=1.0)
    height: float = Field(default=1.0)
    shape: str = Field(default="square", validation_alias=AliasChoices("shape", "photoShape", "photo_shape"))
    fit: str = Field(default="cover", validation_alias=AliasChoices("fit", "photoFit", "photo_fit"))
    focusX: float = Field(default=0.5, validation_alias=AliasChoices("focusX", "focus_x", "focusx"))
    focusY: float = Field(default=0.5, validation_alias=AliasChoices("focusY", "focus_y", "focusy"))
    cornerRadius: float = Field(default=0.12, validation_alias=AliasChoices("cornerRadius", "corner_radius", "cornerradius"))
    hexagonOrientation: str = Field(default="pointy", validation_alias=AliasChoices("hexagonOrientation", "hexagon_orientation", "hexagonorientation"))
    backgroundColor: Optional[str] = Field(default="#E0E0E0", validation_alias=AliasChoices("backgroundColor", "background_color", "backgroundcolor", "bgColor", "bg_color"))
    removeBackground: bool = Field(default=True, validation_alias=AliasChoices("removeBackground", "remove_background", "removebackground", "bgRemove", "bg_remove"))
    placeholderColor: Optional[str] = Field(default=None, validation_alias=AliasChoices("placeholderColor", "placeholder_color", "placeholdercolor"))
    placeholderColorTolerance: Optional[int] = Field(default=None, validation_alias=AliasChoices("placeholderColorTolerance", "placeholder_color_tolerance", "placeholdercolortolerance"))
    placeholderMode: str = Field(default="detect", validation_alias=AliasChoices("placeholderMode", "placeholder_mode", "placeholdermode"))


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    fieldkey: str = Field(validation_alias=AliasChoices("fieldkey", "fieldKey", "field_key", "key"))
    fieldvalue: Optional[str] = Field(default=None, validation_alias=AliasChoices("fieldvalue", "fieldValue", "field_value", "value", "text"))
    x: float
    y: float
    width: float
    height: float
    textColor: Optional[str] = Field(default="#000000", validation_alias=AliasChoices("textColor", "text_color", "textcolor", "color"))
    backgroundColor: Optional[str] = Field(default=None, validation_alias=AliasChoices("backgroundColor", "background_color", "backgroundcolor", "bgColor", "bg_color"))
    align: str = "center"
    verticalAlign: str = Field(default="center", validation_alias=AliasChoices("verticalAlign", "vertical_align", "verticalalign", "valign"))
    overflow: str = "error"
    wrap: bool = True
    maxLines: Optional[int] = Field(default=None, validation_alias=AliasChoices("maxLines", "max_lines", "maxlines", "lines"))
    padding: float = 0.1
    maxFontSize: Optional[int] = Field(default=None, validation_alias=AliasChoices("maxFontSize", "max_font_size", "maxfontsize"))
    minFontSize: Optional[int] = Field(default=None, validation_alias=AliasChoices("minFontSize", "min_font_size", "minfontsize"))
    fontName: Optional[str] = Field(default=None, validation_alias=AliasChoices("fontName", "font_name", "fontname", "font"))


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    templateId: Union[int, str] = Field(validation_alias=AliasChoices("templateId", "template_id", "templateid", "id"))
    templateName: Optional[str] = Field(default=None, validation_alias=AliasChoices("templateName", "template_name", "templatename", "name"))
    categoryId: Optional[Union[int, str]] = Field(default=None, validation_alias=AliasChoices("categoryId", "category_id", "categoryid"))
    userId: Optional[Union[int, str]] = Field(default=None, validation_alias=AliasChoices("userId", "user_id", "userid"))
    templateImageBase64: Optional[str] = Field(default=None, validation_alias=AliasChoices("templateImageBase64", "template_image_base64", "templateimagebase64", "imageBase64", "image_base64"))
    templateImageUrl: Optional[str] = Field(default=None, validation_alias=AliasChoices("templateImageUrl", "template_image_url", "templateimageurl", "imageUrl", "image_url", "url"))
    photoArea: Optional[PhotoAreaDef] = Field(default=None, validation_alias=AliasChoices("photoArea", "photo_area", "photoarea", "photoRegion", "photo_region"))
    photoBase64: Optional[str] = Field(default=None, validation_alias=AliasChoices("photoBase64", "photo_base64", "photobase64", "photo", "image"))
    fields: List[FieldDef] = []
    coordinateMode: str = Field(default="normalized", validation_alias=AliasChoices("coordinateMode", "coordinate_mode", "coordinatemode"))
    outputFormat: str = Field(default="jpeg", validation_alias=AliasChoices("outputFormat", "output_format", "outputformat", "format"))
    quality: Optional[int] = None


class RenderResponse(BaseModel):
    success: bool = True
    templateId: Union[int, str]
    templateName: Optional[str] = None
    categoryId: Optional[Union[int, str]] = None
    userId: Optional[Union[int, str]] = None
    imageFormat: str
    mimeType: str
    width: int
    height: int
    imageBase64: str
    warnings: List[str] = []


class ErrorBody(BaseModel):
    success: bool = False
    error: str
    message: str
    details: Optional[List[Any]] = None
