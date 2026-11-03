from dataclasses import dataclass


@dataclass(frozen=True)
class RoutedMessage:
    language: str
    intent: str


def detect_language(text: str) -> str:
    if any("\u0980" <= char <= "\u09ff" for char in text):
        return "bn-BD"
    return "fr-FR"


def classify_inquiry(text: str) -> str:
    normalized = text.casefold()

    bengali_terms = {
        "furniture": ("আসবাব", "ফার্নিচার", "চেয়ার", "টেবিল", "ডিজাইন"),
        "3d_design": ("3d", "থ্রিডি", "মডেল"),
        "quote": ("দাম", "মূল্য", "কোটেশন", "বাজেট"),
        "callback": ("ফোন করুন", "কল করুন", "কথা বলতে চাই"),
    }
    french_terms = {
        "furniture": ("meuble", "mobilier", "chaise", "table", "banquette"),
        "3d_design": ("3d", "rendu", "modélisation", "conception"),
        "quote": ("prix", "devis", "tarif", "budget"),
        "callback": ("rappelez-moi", "appelez-moi", "rappel", "être appelé"),
    }

    terms = bengali_terms if detect_language(text) == "bn-BD" else french_terms
    for intent, keywords in terms.items():
        if any(keyword in normalized for keyword in keywords):
            return intent
    return "general"


async def route_message(text: str) -> RoutedMessage:
    return RoutedMessage(language=detect_language(text), intent=classify_inquiry(text))


def build_reply(routed: RoutedMessage) -> str:
    if routed.language == "bn-BD":
        replies = {
            "furniture": "বংকেট ডিজাইন সি এইচ আর-এ যোগাযোগ করার জন্য ধন্যবাদ। আপনার বাণিজ্যিক আসবাবের প্রয়োজন, মাপ ও অবস্থান জানালে আমাদের দল সহায়তা করবে।",
            "3d_design": "আপনার 3D ডিজাইন প্রকল্প সম্পর্কে জানানোর জন্য ধন্যবাদ। প্রয়োজনীয় মাপ, রেফারেন্স এবং সময়সীমা পাঠালে আমাদের দল পরবর্তী ধাপ জানাবে।",
            "quote": "কোটেশনের জন্য ধন্যবাদ। অনুগ্রহ করে পণ্যের ধরন, পরিমাণ, মাপ এবং ডেলিভারির শহর জানান।",
            "callback": "আপনি ফোনে কথা বলতে চান বুঝেছি। অনুগ্রহ করে আপনার সুবিধাজনক সময় জানান; কলের আগে আমাদের দল সম্মতি নিশ্চিত করবে।",
            "general": "বংকেট ডিজাইন সি এইচ আর-এ যোগাযোগ করার জন্য ধন্যবাদ। আপনি আসবাব, 3D ডিজাইন, নাকি কোটেশন সম্পর্কে জানতে চান?",
        }
    else:
        replies = {
            "furniture": "Merci d’avoir contacté Banquette Design CHR. Indiquez-nous le type de mobilier, les dimensions et votre ville afin que notre équipe puisse vous aider.",
            "3d_design": "Merci pour votre demande de conception 3D. Envoyez-nous les dimensions, vos références et votre échéance pour connaître les prochaines étapes.",
            "quote": "Merci pour votre demande de devis. Précisez le produit, la quantité, les dimensions et la ville de livraison.",
            "callback": "Nous avons noté votre demande de rappel. Indiquez-nous un créneau qui vous convient; notre équipe confirmera avant de vous appeler.",
            "general": "Merci d’avoir contacté Banquette Design CHR. Souhaitez-vous des informations sur le mobilier, la conception 3D ou un devis ?",
        }
    return replies.get(routed.intent, replies["general"])
