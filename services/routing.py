from dataclasses import dataclass


@dataclass(frozen=True)
class RoutedMessage:
    language: str
    intent: str


def detect_language(text: str) -> str:
    """Detect Bengali script; default to French for supported-language routing."""
    if any("\u0980" <= character <= "\u09ff" for character in text):
        return "bn-BD"
    return "fr-FR"


def classify_inquiry(text: str) -> str:
    """Classify common furniture, 3D design, and quote requests using keywords."""
    normalized = text.casefold()

    bengali_terms = {
        "furniture": ("আসবাব", "ফার্নিচার", "চেয়ার", "টেবিল", "ডিজাইন"),
        "3d_design": ("3d", "থ্রিডি", "মডেল"),
        "quote": ("দাম", "মূল্য", "কোটেশন", "বাজেট"),
    }
    french_terms = {
        "furniture": ("meuble", "mobilier", "chaise", "table", "banquette"),
        "3d_design": ("3d", "rendu", "modélisation", "conception"),
        "quote": ("prix", "devis", "tarif", "budget"),
    }

    language_terms = bengali_terms if detect_language(text) == "bn-BD" else french_terms
    for intent, keywords in language_terms.items():
        if any(keyword in normalized for keyword in keywords):
            return intent
    return "general"


async def route_message(text: str) -> RoutedMessage:
    """Async routing seam for later CRM, queue, and response integrations."""
    return RoutedMessage(
        language=detect_language(text),
        intent=classify_inquiry(text),
    )
