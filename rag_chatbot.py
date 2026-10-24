import re
import pandas as pd
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from deep_translator import GoogleTranslator

MENU_FILE = "menu.xlsx"

# Load menu
df = pd.read_excel(MENU_FILE).fillna("")
df.columns = df.columns.str.strip()

for col in ["Category", "Food Item"]:
    if col in df.columns:
        df[col] = df[col].astype(str).str.strip()

# Load embedding model
print("Loading MAD Food Corner menu...")
model = SentenceTransformer("all-MiniLM-L6-v2")

menu_documents = []

for _, row in df.iterrows():
    menu_documents.append(
        "Category: " + str(row.get("Category", "")) +
        " | Food Item: " + str(row.get("Food Item", "")) +
        " | Full Price: " + str(row.get("Full Price (₹)", "")) +
        " | Half Price: " + str(row.get("Half Price (₹)", ""))
    )

menu_embeddings = model.encode(
    menu_documents,
    normalize_embeddings=True
)


# Format prices
def clean_price(value):
    if value == "" or pd.isna(value):
        return None

    try:
        number = float(value)
        return str(int(number)) if number.is_integer() else str(number)
    except (ValueError, TypeError):
        return str(value)


# Translate text
def translate_text(text, target_language):
    try:
        return GoogleTranslator(
            source="auto",
            target=target_language
        ).translate(text)
    except Exception as e:
        print("Translation temporarily unavailable:", e)
        return None


def telugu_to_english(question):
    q = question.strip()

    # Common menu questions
    replacements = {
        "చికెన్ 65": "Chicken 65",
        "చిల్లీ చికెన్": "Chilli Chicken",
        "చికెన్ ఫ్రైడ్ రైస్": "Chicken Fried Rice",
        "చికెన్ బిర్యానీ": "Chicken Biryani",
        "చికెన్ మంచూరియా": "Chicken Manchuria",
        "చికెన్": "Chicken",
        "వెజ్": "Veg",
        "ధర": "price",
        "ఎంత": "how much",
        "రేటు": "rate",
        "మెనూ": "menu",
        "హాయ్": "hi",
        "హలో": "hello",
        "చూపించు": "show",
        "ఏమున్నాయి": "what available"
    }

    for telugu, english in replacements.items():
        q = q.replace(telugu, english)

    return q


# Return answer in customer's language
def make_answer(answer, is_telugu):
    if is_telugu:
        translated_answer = translate_text(answer, "te")
        if translated_answer:
            return translated_answer
    return answer
    # Detect Telugu text
def contains_telugu(text):
    return any(
        "\u0C00" <= char <= "\u0C7F"
        for char in text
    )


# Convert common Telugu menu words into English
def telugu_to_english(question):
    replacements = {
        "చికెన్ 65": "Chicken 65",
        "చిల్లీ చికెన్": "Chilli Chicken",
        "చికెన్ ఫ్రైడ్ రైస్": "Chicken Fried Rice",
        "చికెన్ బిర్యానీ": "Chicken Biryani",
        "చికెన్": "Chicken",
        "వెజ్": "Veg",
        "ధర": "price",
        "ఎంత": "how much",
        "రేటు": "rate",
        "మెనూ": "menu",
        "హాయ్": "hi",
        "హలో": "hello",
        "చూపించు": "show",
        "ఏమున్నాయి": "what available"
    }

    for telugu, english in replacements.items():
        question = question.replace(telugu, english)

    return question


# Main chatbot
def ask_menu(question, top_k=3):
    question = question.strip()

    if not question:
        return "Please type a question."

       # Detect language
    is_telugu = contains_telugu(question)

    # Convert common Telugu menu words into English
    if is_telugu:
        search_question = telugu_to_english(question)
    else:
        search_question = question

    # Prepare question for searching
    q = search_question.lower().strip()

    # 1. Greetings
    greetings = {
        "hi", "hii", "hiii", "hello", "hey",
        "hai", "good morning", "good afternoon",
        "good evening"
    }

    if re.sub(r"[!?. ]+", "", q) in greetings:
        answer = (
            "Hello! Welcome to MAD Food Corner! 🍗\n"
            "How can I help you today?\n"
            "You can ask about menu items, prices, "
            "veg starters or non-veg starters."
        )
        return make_answer(answer, is_telugu)

    # 2. Find exact food item
    matched_item = None

    items = sorted(
        df["Food Item"].dropna().astype(str).unique(),
        key=len,
        reverse=True
    )

    for item in items:
        if item.strip() and item.lower() in q:
            matched_item = item
            break

    # 3. Price questions
    price_words = [
        "price", "cost", "how much", "rate",
        "ధర", "ఎంత"
    ]

    if any(word in q for word in price_words):
        if matched_item:
            row = df[
                df["Food Item"].str.lower() == matched_item.lower()
            ].iloc[0]

            full = clean_price(row.get("Full Price (₹)", ""))
            half = clean_price(row.get("Half Price (₹)", ""))

            prices = []

            if full:
                prices.append(f"Full: ₹{full}")

            if half:
                prices.append(f"Half: ₹{half}")

            if prices:
                answer = (
                    f"The price of {matched_item} is "
                    + " and ".join(prices) + "."
                )
            else:
                answer = (
                    f"Sorry, the price of {matched_item} "
                    "is not available."
                )

            return make_answer(answer, is_telugu)

        answer = (
            "Which food item's price would you like to know? "
            "Please mention the food item name."
        )
        return make_answer(answer, is_telugu)

    # 4. Exact food item details
    if matched_item:
        row = df[
            df["Food Item"].str.lower() == matched_item.lower()
        ].iloc[0]

        category = str(row.get("Category", ""))
        full = clean_price(row.get("Full Price (₹)", ""))
        half = clean_price(row.get("Half Price (₹)", ""))

        answer = f"Food item: {matched_item}"

        if category:
            answer += f"\nCategory: {category}"

        if full:
            answer += f"\nFull price: ₹{full}"

        if half:
            answer += f"\nHalf price: ₹{half}"

        return make_answer(answer, is_telugu)

    # 5. Category-based questions
    for category in df["Category"].dropna().astype(str).unique():
        if category.strip() and category.lower() in q:
            results = df[
                df["Category"].str.lower() == category.lower()
            ]

            names = results["Food Item"].tolist()

            if names:
                answer = (
                    f"Here are our {category} items:\n"
                    + "\n".join(f"- {name}" for name in names)
                )
                return make_answer(answer, is_telugu)

    # 6. General RAG retrieval
    query_embedding = model.encode(
        [search_question],
        normalize_embeddings=True
    )

    scores = cosine_similarity(
        query_embedding,
        menu_embeddings
    )[0]

    indices = scores.argsort()[::-1][:top_k]

    results = [
        menu_documents[index]
        for index in indices
    ]

    answer = (
        "Here is some menu information that may help:\n"
        + "\n".join(results)
        + "\nFor an exact price, please mention the food item name."
    )

    return make_answer(answer, is_telugu)


# Run chatbot
if __name__ == "__main__":
    print("\nMAD Food Corner Chatbot")
    print("Type 'exit' to stop.")

    while True:
        question = input("\nCustomer: ")

        if question.lower().strip() == "exit":
            break

        print("\nBot:", ask_menu(question))
