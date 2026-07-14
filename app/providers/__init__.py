"""AI provider implementations for Prompt Relay.

Every provider conforms to the interfaces in ``base.py`` so they can be swapped
or chained behind the fallback wrappers without the rest of the app caring which
concrete backend served a given request.
"""
