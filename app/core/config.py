from pydantic_settings import BaseSettings
from decouple import config
from functools import lru_cache

class Settings(BaseSettings):
    APP_NAME: str = config("APP_NAME", default="AI Chat Application")
    DEBUG: bool = config("DEBUG", default=False, cast=bool)
    ENVIRONMENT: str = config("ENVIRONMENT", default="development")
    
    # Security
    SECRET_KEY: str = config("SECRET_KEY")
    ALGORITHM: str = config("ALGORITHM", default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = config(
        "ACCESS_TOKEN_EXPIRE_MINUTES", default=30, cast=int
    )
    
    # Database
    POSTGRES_USER: str = config("POSTGRES_USER")
    POSTGRES_PASSWORD: str = config("POSTGRES_PASSWORD")
    POSTGRES_DB: str = config("POSTGRES_DB")
    POSTGRES_HOST: str = config("POSTGRES_HOST", default="ai-chat-db")
    POSTGRES_PORT: str = config("POSTGRES_PORT", default="5432")
    
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
    
    # Descope Authentication
    DESCOPE_PROJECT_ID: str = config("DESCOPE_PROJECT_ID")
    DESCOPE_MANAGEMENT_KEY: str = config("DESCOPE_MANAGEMENT_KEY")
    ALLOWED_SOCIAL_PROVIDERS: str = config(
        "ALLOWED_SOCIAL_PROVIDERS",
        default="google,github,microsoft"
    )

    # Google OAuth Configuration
    GOOGLE_CLIENT_ID: str = config("GOOGLE_CLIENT_ID", default="")
    GOOGLE_CLIENT_SECRET: str = config("GOOGLE_CLIENT_SECRET", default="")
    GOOGLE_REDIRECT_URI: str = config("GOOGLE_REDIRECT_URI", default="")

    # Stripe Configuration
    STRIPE_SECRET_KEY: str = config("STRIPE_SECRET_KEY")
    STRIPE_PUBLISHABLE_KEY: str = config("STRIPE_PUBLISHABLE_KEY")
    STRIPE_WEBHOOK_SECRET: str = config("STRIPE_WEBHOOK_SECRET")
    STRIPE_CUSTOMER_WEBHOOK_SECRET: str = config("STRIPE_CUSTOMER_WEBHOOK_SECRET")
    STRIPE_CUSTOMER_SUBSCRIPTION_WEBHOOK_SECRET: str = config("STRIPE_CUSTOMER_SUBSCRIPTION_WEBHOOK_SECRET")
    STRIPE_FREE_TIER_PRICE_ID: str = config("STRIPE_FREE_TIER_PRICE_ID")

    # Weaviate Configuration
    WEAVIATE_URL: str = config("WEAVIATE_URL")
    WEAVIATE_API_KEY: str = config("WEAVIATE_API_KEY")
    HUGGINGFACE_API_KEY: str = config("HUGGINGFACE_API_KEY")

    # Langfuse
    LANGFUSE_SECRET_KEY: str = config("LANGFUSE_SECRET_KEY")
    LANGFUSE_PUBLIC_KEY: str = config("LANGFUSE_PUBLIC_KEY")
    LANGFUSE_HOST: str = config("LANGFUSE_HOST", default="https://cloud.langfuse.com")

    # LLMs  
    OPENAI_API_KEY: str = config("OPENAI_API_KEY")
    PRIMARY_LLM_PROVIDER: str = config("PRIMARY_LLM_PROVIDER", default="openai")
    OPENAI_MODEL_NAME: str = config("OPENAI_MODEL_NAME", default="gpt-4o")
    LLM_REQUEST_TIMEOUT: int = 120

    # Llama Index Configuration
    LLAMA_PARSE_KEY: str = config("LLAMA_PARSE_KEY")

    # Tiptap Cloud Configuration
    TIPTAP_CLOUD_APP_ID: str = config("TIPTAP_CLOUD_APP_ID")
    TIPTAP_CLOUD_API_SECRET_KEY: str = config("TIPTAP_CLOUD_API_SECRET_KEY")
    # JWT Configuration
    JWT_AI_SECRET: str = config("JWT_AI_SECRET")
    JWT_COLLAB_SECRET: str = config("JWT_COLLAB_SECRET")

    # Google Cloud Storage Configuration
    GCS_PROJECT_ID: str = config("GCS_PROJECT_ID")
    USE_GCP_WORKLOAD_IDENTITY: bool = config("USE_GCP_WORKLOAD_IDENTITY", default=False, cast=bool)
    GCS_CREDENTIALS_JSON: str = config("GCS_CREDENTIALS_JSON", default="")
    
    # Email Configuration with SendGrid
    SENDGRID_API_KEY: str = config("SENDGRID_API_KEY")
    EMAIL_SENDER: str = config("EMAIL_SENDER", default="notifications-noreply@plumloom.ai")

    def get_gcp_credentials(self):
        """Get GCP credentials based on environment"""
        from google.cloud import storage
        from google.oauth2 import service_account
        
        if self.USE_GCP_WORKLOAD_IDENTITY:
            # Use Workload Identity in production
            return storage.Client(project=self.GCS_PROJECT_ID)
        elif self.GCS_CREDENTIALS_JSON:
            # Use service account JSON if provided
            credentials = service_account.Credentials.from_service_account_file(
                self.GCS_CREDENTIALS_JSON
            )
            return storage.Client(
                project=self.GCS_PROJECT_ID,
                credentials=credentials
            )
        else:
            # Use Application Default Credentials for local development
            return storage.Client(project=self.GCS_PROJECT_ID)

    @property
    def GCS_DOCUMENT_PREFIX(self) -> str:
        """Prefix for document storage in GCS bucket"""
        env_prefix = "dev" if self.ENVIRONMENT == "development" else "prod"
        return f"{env_prefix}/documents"
    
    @property
    def use_gcp_default_credentials(self) -> bool:
        """Whether to use GCP default credentials (Workload Identity)"""
        # In production, always use Workload Identity
        # In development, use credentials.json
        return self.ENVIRONMENT == "production"

@lru_cache()
def get_settings() -> Settings:
    return Settings()
