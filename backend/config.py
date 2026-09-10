from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        populate_by_name=True,
        extra="ignore",
    )

    catalog: str = Field(default="deep_test_1_catalog", validation_alias="PET_CATALOG")
    # "schema" is safe as a field name in pydantic v2 (model_json_schema() replaced .schema()).
    # validation_alias tells pydantic-settings which env var to read.
    schema: str = Field(default="lactalis_pet_planner", validation_alias="PET_SCHEMA")
    warehouse_id: str = Field(default="", validation_alias="DATABRICKS_WAREHOUSE_ID")
    genie_space_id: str = Field(default="", validation_alias="PET_GENIE_SPACE_ID")
    host: str = Field(default="", validation_alias="DATABRICKS_HOST")


settings = Settings()
