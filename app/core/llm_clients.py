# app/core/llm_clients.py

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

import httpx
from fastapi import HTTPException
from openai import AsyncOpenAI, OpenAIError
from langfuse.client import Langfuse # Import Langfuse types if needed later

from app.core.config import get_settings
from app.core.logging_config import logger

settings = get_settings()

# Ensure this exception exists or define it
from app.core.exceptions import LLMGenerationError



class BaseLLMClient(ABC):
    """Abstract base class for LLM clients."""
    provider_name: str

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        # Add other common parameters like temperature, max_tokens if needed
        **kwargs: Any
    ) -> str:
        """Generate text based on the prompt."""
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Return the specific model name being used."""
        pass


class OpenAIClient(BaseLLMClient):
    """Client for OpenAI API."""
    provider_name = "openai"

    def __init__(self, api_key: str = settings.OPENAI_API_KEY, model_name: str = settings.OPENAI_MODEL_NAME):
        if not api_key:
             logger.error("OpenAI API key is not configured.")
             # It might be better to raise this error when the client is actually needed,
             # but raising it here prevents instantiation with missing config.
             raise RuntimeError("OpenAI API key is not configured.")

        try:
            self._client = AsyncOpenAI(api_key=api_key)
            self._model_name = model_name
            logger.info(f"Initialized OpenAIClient with model: {self._model_name}")
        except Exception as e:
             logger.error(f"Failed to initialize OpenAIClient: {e}", exc_info=True)
             # Handle initialization failure appropriately
             raise RuntimeError(f"OpenAIClient initialization failed: {e}") from e


    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs: Any
    ) -> str:
        # Prioritize 'messages' from kwargs if available
        final_messages = kwargs.pop("messages", None)

        if final_messages is None:
            final_messages = []
            if system_prompt:
                final_messages.append({"role": "system", "content": system_prompt})
            if prompt:
                final_messages.append({"role": "user", "content": prompt})
        
        # Ensure final_messages is not empty before proceeding to API call
        if not final_messages:
            logger.error("No messages to send to OpenAI API. 'messages' kwarg was empty or not provided, and prompt/system_prompt were also insufficient.")
            raise LLMGenerationError("Cannot call OpenAI API with no messages.", provider=self.provider_name)

        try:
            response = await self._client.chat.completions.create(
                model=self._model_name,
                messages=final_messages,
                timeout=settings.LLM_REQUEST_TIMEOUT,
                **kwargs # Pass remaining kwargs (messages has been popped)
            )
            content = response.choices[0].message.content
            if content is None:
                logger.error("OpenAI response content is None.")
                raise LLMGenerationError("OpenAI returned an empty response.", provider=self.provider_name)
            logger.debug("OpenAI API call successful.")
            return content.strip()
        except OpenAIError as e:
            logger.error(f"OpenAI API error: {e}", exc_info=True)
            # Check if it's an authentication error (e.g., invalid key)
            if hasattr(e, 'status_code') and e.status_code in [401, 403]:
                 detail = f"OpenAI Authentication Error: {e}"
            else:
                 detail = f"OpenAI API error: {e}"
            raise LLMGenerationError(detail, provider=self.provider_name) from e
        except httpx.ReadTimeout:
             logger.error(f"OpenAI API request timed out after {settings.LLM_REQUEST_TIMEOUT}s.")
             raise LLMGenerationError("OpenAI request timed out.", provider=self.provider_name)
        except Exception as e:
            logger.error(f"Unexpected error during OpenAI call: {e}", exc_info=True)
            raise LLMGenerationError(f"Unexpected error during LLM call: {e}", provider=self.provider_name) from e

    def get_model_name(self) -> str:
        return self._model_name





# --- Factory/Dependency Function ---

# Removed the caching dictionary _llm_clients.
# FastAPI's dependency injection handles caching/scoping of dependencies.
# Creating a new client instance per request (if not cached by FastAPI) is generally safe
# for stateless clients like these. If initialization is expensive,
# dependency injection configuration should handle it.

def get_llm_client(provider: str = "openai") -> BaseLLMClient:
    """Factory function to get LLM client instance."""
    # Clean the input provider string
    provider = provider.lower().strip()
    # logger.debug(f"Requesting LLM client for provider: '{provider}'") # Can be too verbose

    # Only support OpenAI
    if provider == "openai":
        try:
            # Pass API key and model name explicitly from settings
            return OpenAIClient(api_key=settings.OPENAI_API_KEY, model_name=settings.OPENAI_MODEL_NAME)
        except RuntimeError as e:
             logger.critical(f"Failed to create OpenAIClient instance: {e}")
             raise HTTPException(status_code=503, detail=f"LLM provider '{provider}' unavailable: {e}")
        except Exception as e:
             logger.critical(f"Unexpected error initializing OpenAIClient: {e}")
             raise HTTPException(status_code=500, detail=f"Internal server error initializing LLM client: {e}")
    else:
        logger.error(f"Unsupported LLM provider requested: '{provider}'. Only 'openai' is supported.")
        raise HTTPException(status_code=501, detail=f"Unsupported LLM provider: {provider}. Only 'openai' is supported.")


def get_primary_llm_client() -> BaseLLMClient:
    """Dependency to get the primary configured LLM client."""
    # This relies on FastAPI's dependency injection to manage the lifecycle of the client
    return get_llm_client(settings.PRIMARY_LLM_PROVIDER)