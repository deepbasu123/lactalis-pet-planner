"""backend/config.py — environment configuration for the thin serving layer."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")

    catalog: str = Field(default="deep_test_1_catalog", validation_alias="PET_CATALOG")
    silver_schema: str = Field(default="lactalis_pet_silver", validation_alias="PET_SILVER_SCHEMA")
    gold_schema: str = Field(default="lactalis_pet_gold", validation_alias="PET_GOLD_SCHEMA")
    # Warehouse the app runs Gold SQL on. DATABRICKS_WAREHOUSE_ID is the App's
    # injected resource var; PET_WAREHOUSE_ID overrides locally.
    warehouse_id: str = Field(default="", validation_alias="PET_WAREHOUSE_ID")
    warehouse_id_app: str = Field(default="", validation_alias="DATABRICKS_WAREHOUSE_ID")
    pipeline_id: str = Field(default="", validation_alias="PET_PIPELINE_ID")
    volume: str = Field(
        default="deep_test_1_catalog.lactalis_pet_bronze.landing",
        validation_alias="PET_VOLUME",
    )
    genie_space_id: str = Field(default="", validation_alias="PET_GENIE_SPACE_ID")

    @property
    def effective_warehouse_id(self) -> str:
        return self.warehouse_id or self.warehouse_id_app


settings = Settings()
