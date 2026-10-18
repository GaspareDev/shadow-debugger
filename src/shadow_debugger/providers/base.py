"""Provider abstraction: one small class per LLM backend, discovered through a registry."""

from __future__ import annotations

import abc
import os
from typing import Callable, ClassVar, Dict, List, Optional, Sequence, Type, TypeVar


class ProviderError(Exception):
    """Raised when an LLM backend cannot produce an analysis (config, network, API error)."""


class Provider(abc.ABC):
    """Base class for LLM backends.

    Subclasses implement :meth:`complete`. To add a backend, subclass, decorate with
    :func:`register_provider` and import the module in ``providers/__init__.py``::

        @register_provider("mistral")
        class MistralProvider(Provider):
            default_model = "mistral-small-latest"
            env_key = "MISTRAL_API_KEY"

            def complete(self, system: str, prompt: str) -> str:
                ...
    """

    name: ClassVar[str] = ""
    default_model: ClassVar[str] = ""
    env_key: ClassVar[Optional[str]] = None
    requires_api_key: ClassVar[bool] = True

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 60.0,
        fallback_models: Sequence[str] = (),
    ) -> None:
        self.model = model or self.default_model
        self.api_key = api_key or (os.getenv(self.env_key) if self.env_key else None)
        self.base_url = base_url
        self.timeout = timeout
        self.fallback_models: List[str] = [m for m in fallback_models if m and m != self.model]

    def check_configuration(self) -> None:
        """Raise ProviderError early if the provider is obviously misconfigured."""
        if self.requires_api_key and not self.api_key:
            hint = f" or set the {self.env_key} environment variable" if self.env_key else ""
            raise ProviderError(f"{self.name}: missing API key. Pass api_key to init(){hint}.")

    @abc.abstractmethod
    def complete(self, system: str, prompt: str) -> str:
        """Return the model's text answer or raise :class:`ProviderError`."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model!r})"


_REGISTRY: Dict[str, Type[Provider]] = {}

P = TypeVar("P", bound=Type[Provider])


def register_provider(*names: str) -> Callable[[P], P]:
    """Class decorator registering a provider under one or more names."""

    def decorator(cls: P) -> P:
        if not names:
            raise ValueError("register_provider() needs at least one name")
        cls.name = cls.name or names[0]
        for n in names:
            _REGISTRY[n.lower()] = cls
        return cls

    return decorator


def available_providers() -> List[str]:
    return sorted(_REGISTRY)


def create_provider(name: str, **kwargs) -> Provider:
    key = name.lower().strip()
    if key not in _REGISTRY:
        raise ValueError(
            f"Unknown provider {name!r}. Available: {', '.join(available_providers())}. "
            "You can also pass a Provider instance."
        )
    return _REGISTRY[key](**kwargs)
