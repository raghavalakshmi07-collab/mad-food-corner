
import os
import html
import uuid
import sqlite3
import random
import secrets

import pandas as pd
from flask import (
    Flask, render_template, session, redirect,
    url_for, request, jsonify
)
from functools import wraps
from rag_chatbot import ask_menu


app = Flask(__name__)

app.secret_key = os.environ.get(
    "MAD_SECRET_KEY"
) or secrets.token_hex(32)

OWNER_USERNAME = "owner"
OWNER_PASSWORD = os.environ.get("MAD_OWNER_PASSWORD")

def owner_required(view_function):
    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if not session.get("owner_logged_in"):
            return redirect(url_for("owner_login"))
        return view_function(*args, **kwargs)
    return wrapped_view


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MENU_FILE = os.path.join(BASE_DIR, "menu.xlsx")
ORDERS_DB = os.path.join(BASE_DIR, "orders.db")
REVIEWS_DB = os.path.join(BASE_DIR, "reviews.db")


# --------------------------------------------------
# DATABASE INITIALIZATION
# --------------------------------------------------

def init_orders_db():
    with sqlite3.connect(ORDERS_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                address TEXT NOT NULL,
                total REAL NOT NULL,
                payment TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS spin_results (
                spin_id TEXT PRIMARY KEY,
                offer TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


def init_reviews_db():
    with sqlite3.connect(REVIEWS_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL,
                rating INTEGER NOT NULL,
                comment TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


init_orders_db()
init_reviews_db()


# --------------------------------------------------
# MENU HELPERS
# --------------------------------------------------

def get_menu():
    if not os.path.exists(MENU_FILE):
        raise FileNotFoundError(
            f"Menu Excel file not found: {MENU_FILE}"
        )

    df = pd.read_excel(MENU_FILE).fillna("")
    df.columns = df.columns.astype(str).str.strip()

    if "Images" in df.columns and "Image" not in df.columns:
        df = df.rename(columns={"Images": "Image"})

    return df.to_dict(orient="records")


def get_price(item, size):
    column = (
        "Full Price (₹)"
        if size == "Full"
        else "Half Price (₹)"
    )

    value = item.get(column, "")

    if value == "":
        return None

    try:
        price = float(value)
        return price if price >= 0 else None
    except (ValueError, TypeError):
        return None


def get_cart_count():
    cart = session.get("cart", {})

    return sum(
        entry.get("quantity", 0)
        for entry in cart.values()
        if isinstance(entry, dict)
        and isinstance(entry.get("quantity"), int)
        and entry.get("quantity", 0) > 0
    )


def get_valid_cart():
    """Return valid cart items and the current subtotal."""
    items = get_menu()
    cart = session.get("cart", {})
    cart_items = []
    subtotal = 0.0

    for key, entry in cart.items():
        if not isinstance(entry, dict):
            continue

        item_id = entry.get("item_id")
        quantity = entry.get("quantity")
        size = entry.get("size", "Full")

        if (
            not isinstance(item_id, int)
            or not 0 <= item_id < len(items)
            or not isinstance(quantity, int)
            or quantity <= 0
            or size not in ("Full", "Half")
        ):
            continue

        price = get_price(items[item_id], size)

        if price is None:
            continue

        item = items[item_id]
        line_total = round(price * quantity, 2)
        subtotal += line_total

        cart_items.append({
            "name": item.get("Food Item", "Food"),
            "image": str(item.get("Image", "")).strip(),
            "size": size,
            "price": price,
            "quantity": quantity,
            "subtotal": line_total,
            "item_id": item_id,
            "key": key
        })

    return cart_items, round(subtotal, 2)


# --------------------------------------------------
# SPIN & WIN
# --------------------------------------------------

SPIN_OFFERS = [
    {"label": "5% OFF", "type": "discount", "value": 5},
    {"label": "10% OFF", "type": "discount", "value": 10},
    {"label": "15% OFF", "type": "discount", "value": 15},
    {"label": "20% OFF", "type": "discount", "value": 20},
    {"label": "FREE FRIES", "type": "free_item", "value": "Fries"},
    {"label": "FREE DRINK", "type": "free_item", "value": "Drink"},
]


def calculate_spin_offer(subtotal):
    saved_offer = session.get("spin_offer")

    if not isinstance(saved_offer, dict):
        return None, 0.0, round(subtotal, 2)

    offer = next(
        (
            item for item in SPIN_OFFERS
            if item["label"] == saved_offer.get("label")
        ),
        None
    )

    if offer is None:
        return None, 0.0, round(subtotal, 2)

    discount = 0.0

    if offer["type"] == "discount":
        discount = round(
            subtotal * offer["value"] / 100, 2
        )

    final_total = round(subtotal - discount, 2)

    return offer, discount, final_total


@app.route("/spin", methods=["POST"])
def spin():
    spin_id = session.get("spin_id")

    if not spin_id:
        spin_id = uuid.uuid4().hex
        session["spin_id"] = spin_id

    with sqlite3.connect(ORDERS_DB) as conn:
        existing = conn.execute(
            "SELECT offer FROM spin_results WHERE spin_id = ?",
            (spin_id,)
        ).fetchone()

        if existing:
            return jsonify({
                "message": "You have already used your spin!"
            }), 409

        selected = random.choice(SPIN_OFFERS)

        conn.execute(
            """
            INSERT INTO spin_results (spin_id, offer)
            VALUES (?, ?)
            """,
            (spin_id, selected["label"])
        )

    session["spin_offer"] = selected
    session.modified = True

    selected_index = SPIN_OFFERS.index(selected)
    rotation = (
        360 * 5
        + (360 - (selected_index * 60 + 30))
    )

    return jsonify({
        "offer": selected["label"],
        "type": selected["type"],
        "value": selected["value"],
        "rotation": rotation
    })


# --------------------------------------------------
# HOME PAGE AND SEARCH
# --------------------------------------------------

@app.route("/")
def home():
    all_items = get_menu()

    for index, item in enumerate(all_items):
        item["_item_id"] = index

    search_query = request.args.get("search", "").strip()

    if search_query:
        query = " ".join(search_query.casefold().split())

        items = [
            item for item in all_items
            if query in " ".join(
                str(item.get("Food Item", ""))
                .casefold().split()
            )
        ]
    else:
        items = all_items

    return render_template(
        "index.html",
        items=items,
        cart_count=get_cart_count(),
        search_query=search_query
    )


@app.route("/gallery")
def gallery():
    return render_template("gallery.html")


# --------------------------------------------------
# CART
# --------------------------------------------------

@app.route("/add-to-cart/<int:item_id>", methods=["POST"])
def add_to_cart(item_id):
    items = get_menu()

    if not 0 <= item_id < len(items):
        return "Food item not found.", 404

    size = request.form.get("size", "Full")

    if size not in ("Full", "Half"):
        size = "Full"

    price = get_price(items[item_id], size)

    if price is None:
        return "Price not available for this size.", 400

    cart = session.get("cart", {})
    key = f"{item_id}_{size}"

    if key in cart and isinstance(cart[key], dict):
        cart[key]["quantity"] += 1
    else:
        cart[key] = {
            "item_id": item_id,
            "size": size,
            "price": price,
            "quantity": 1
        }

    session["cart"] = cart
    session.modified = True

    return redirect(url_for("home"))


@app.route("/cart")
def view_cart():
    cart_items, total = get_valid_cart()

    offer, discount_amount, final_total = (
        calculate_spin_offer(total)
    )

    return render_template(
        "cart.html",
        cart_items=cart_items,
        total=total,
        discount_amount=discount_amount,
        final_total=final_total,
        spin_offer=offer,
        cart_count=get_cart_count()
    )


@app.route("/update-cart/<path:cart_key>", methods=["POST"])
def update_cart(cart_key):
    cart = session.get("cart", {})
    action = request.form.get("action")

    entry = cart.get(cart_key)

    if isinstance(entry, dict):
        quantity = entry.get("quantity", 0)

        if not isinstance(quantity, int) or quantity <= 0:
            cart.pop(cart_key, None)

        elif action == "increase":
            entry["quantity"] = quantity + 1

        elif action == "decrease":
            if quantity > 1:
                entry["quantity"] = quantity - 1
            else:
                cart.pop(cart_key, None)

    session["cart"] = cart
    session.modified = True

    return redirect(url_for("view_cart"))


@app.route("/remove-from-cart/<path:cart_key>", methods=["POST"])
def remove_from_cart(cart_key):
    cart = session.get("cart", {})
    cart.pop(cart_key, None)

    session["cart"] = cart
    session.modified = True

    return redirect(url_for("view_cart"))


# --------------------------------------------------
# CHECKOUT
# --------------------------------------------------

@app.route("/checkout")
def checkout():
    cart_items, total = get_valid_cart()

    if not cart_items or total <= 0:
        return redirect(url_for("view_cart"))

    offer, discount_amount, final_total = (
        calculate_spin_offer(total)
    )

    return render_template(
        "checkout.html",
        total=total,
        discount_amount=discount_amount,
        final_total=final_total,
        spin_offer=offer,
        cart_count=get_cart_count()
    )


# --------------------------------------------------
# PLACE ORDER
# --------------------------------------------------

@app.route("/place-order", methods=["POST"])
def place_order():
    customer_name = request.form.get(
        "customer_name", ""
    ).strip()

    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()
    payment = request.form.get("payment", "COD")

    if (
        not customer_name
        or len(customer_name) > 80
        or not address
        or len(address) > 1000
    ):
        return "Enter a valid name and delivery address.", 400

    if not phone.isdigit() or len(phone) != 10:
        return "Enter a valid 10-digit mobile number.", 400

    if payment not in ("COD", "UPI"):
        return "Invalid payment method.", 400

    cart_items, subtotal = get_valid_cart()

    if not cart_items or subtotal <= 0:
        return redirect(url_for("view_cart"))

    offer, discount_amount, final_total = (
        calculate_spin_offer(subtotal)
    )

    payment_label = (
        "Cash on Delivery"
        if payment == "COD"
        else "UPI (not verified)"
    )

    order_id = "MAD-" + uuid.uuid4().hex[:12].upper()

    # Save the order in the database.
    with sqlite3.connect(ORDERS_DB) as conn:
        conn.execute(
            """
            INSERT INTO orders
            (order_id, customer_name, phone, address,
             total, payment, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                customer_name,
                phone,
                address,
                final_total,
                payment_label,
                "Pending"
            )
        )

    # Prepare safe HTML for the confirmation page.
    safe_name = html.escape(customer_name)
    safe_phone = html.escape(phone)
    safe_address = html.escape(address)

    order_lines = []

    for item in cart_items:
        order_lines.append(
            "<li>"
            + html.escape(str(item["name"]))
            + " (" + html.escape(item["size"]) + ")"
            + f" × {item['quantity']} — "
            + f"₹{item['subtotal']:.2f}"
            + "</li>"
        )

    if offer and offer["type"] == "free_item":
        order_lines.append(
            "<li>🎁 "
            + html.escape(offer["value"])
            + " — FREE (Spin & Win Offer)</li>"
        )

    offer_label = (
        html.escape(offer["label"])
        if offer else "No offer"
    )

    # Clear cart and applied offer after saving the order.
    session.pop("cart", None)
    session.pop("spin_offer", None)
    session.modified = True

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport"
              content="width=device-width, initial-scale=1.0">
        <title>Order Confirmed - MAD Food Corner</title>
        <style>
            body {{
                margin: 0;
                padding: 30px 15px;
                font-family: Arial, sans-serif;
                background: #8B0000;
                color: #333;
                text-align: center;
            }}
            .confirmation {{
                max-width: 600px;
                margin: 20px auto;
                padding: 30px;
                background: #FFF8F0;
                border-radius: 15px;
                box-shadow: 0 5px 20px rgba(0,0,0,.25);
            }}
            h1, h2, h3 {{ color: #8B0000; }}
            .order-items {{
                text-align: left;
                line-height: 1.8;
            }}
            .order-id {{
                background: #FFE4C4;
                padding: 12px;
                border-radius: 8px;
                overflow-wrap: anywhere;
            }}
            .menu-button {{
                display: inline-block;
                margin: 10px 5px;
                padding: 12px 20px;
                background: #E67E22;
                color: white;
                text-decoration: none;
                border-radius: 6px;
                font-weight: bold;
            }}
        </style>
    </head>
    <body>
        <div class="confirmation">
            <h1>🎉 Order Confirmed!</h1>
            <p>Thank you, <strong>{safe_name}</strong>!</p>
            <h3 class="order-id">Order ID: {order_id}</h3>

            <h3>Your Items</h3>
            <ul class="order-items">
                {"".join(order_lines)}
            </ul>

            <p>Subtotal: ₹{subtotal:.2f}</p>
            <p>Spin & Win Offer: {offer_label}</p>
            <p>Discount: -₹{discount_amount:.2f}</p>
            <h2>Final Total: ₹{final_total:.2f}</h2>

            <p>Mobile: {safe_phone}</p>
            <p>Delivery Address: {safe_address}</p>
            <p>Payment: {html.escape(payment_label)}</p>

            <a href="/track-order" class="menu-button">
                Track Your Order
            </a>
            <a href="/" class="menu-button">Back to Home</a>
        </div>
    </body>
    </html>
    """


# --------------------------------------------------
# TRACK ORDER
# --------------------------------------------------

@app.route("/track-order", methods=["GET", "POST"])
def track_order():
    order = None
    error_message = None

    if request.method == "POST":
        order_id = request.form.get(
            "order_id", ""
        ).strip().upper()

        if order_id:
            with sqlite3.connect(ORDERS_DB) as conn:
                conn.row_factory = sqlite3.Row

                order = conn.execute(
                    """
                    SELECT order_id, customer_name, total,
                           status, created_at
                    FROM orders
                    WHERE order_id = ?
                    """,
                    (order_id,)
                ).fetchone()

        if not order:
            error_message = (
                "Order ID not found. Please check and try again."
            )

    return render_template(
        "track_order.html",
        order=order,
        error_message=error_message
    )


# --------------------------------------------------
# OWNER LOGIN AND DASHBOARD
# --------------------------------------------------

def owner_required(function):
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if not session.get("owner_logged_in"):
            return redirect(url_for("owner_login"))
        return function(*args, **kwargs)

    return decorated_function


@app.route("/owner-login", methods=["GET", "POST"])
def owner_login():
    error_message = None

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if not OWNER_PASSWORD:
            error_message = (
                "Owner password is not configured. "
                "Set MAD_OWNER_PASSWORD before starting Flask."
            )
        elif (
            secrets.compare_digest(username, OWNER_USERNAME)
            and secrets.compare_digest(password, OWNER_PASSWORD)
        ):
            session["owner_logged_in"] = True
            return redirect(url_for("owner_dashboard"))
        else:
            error_message = "Invalid username or password."

    return render_template(
        "owner_login.html",
        error_message=error_message
    )


@app.route("/owner-dashboard")
@owner_required
def owner_dashboard():
    with sqlite3.connect(ORDERS_DB) as conn:
        conn.row_factory = sqlite3.Row

        orders = conn.execute(
            """
            SELECT order_id, customer_name, phone, address,
                   total, payment, status, created_at
            FROM orders
            ORDER BY created_at DESC
            """
        ).fetchall()

    return render_template(
        "dashboard.html",
        orders=orders
    )



@app.route("/update-order-status", methods=["POST"])
@owner_required
def update_order_status():
    order_id = request.form.get("order_id", "").strip()
    status = request.form.get("status", "").strip()

    allowed_statuses = [
        "Pending", "Preparing", "Delivered"
    ]

    if not order_id or status not in allowed_statuses:
        return "Invalid order ID or status.", 400

    with sqlite3.connect(ORDERS_DB) as conn:
        cursor = conn.execute(
            """
            UPDATE orders
            SET status = ?
            WHERE order_id = ?
            """,
            (status, order_id)
        )

        if cursor.rowcount == 0:
            return "Order not found.", 404

    return redirect(url_for("owner_dashboard"))


@app.route("/owner-logout")
def owner_logout():
    session.pop("owner_logged_in", None)
    return redirect(url_for("owner_login"))


# --------------------------------------------------
# CUSTOMER REVIEWS AND LOCATION
# --------------------------------------------------

@app.route("/location", methods=["GET", "POST"])
def location():
    error_message = None

    if request.method == "POST":
        customer_name = request.form.get(
            "customer_name", ""
        ).strip()

        comment = request.form.get("comment", "").strip()

        try:
            rating = int(request.form.get("rating", "0"))
        except (ValueError, TypeError):
            rating = 0

        if not customer_name or not comment:
            error_message = "Enter your name and comments."
        elif len(customer_name) > 80 or len(comment) > 1000:
            error_message = "Name or comment is too long."
        elif rating not in (1, 2, 3, 4, 5):
            error_message = "Select a valid rating."
        else:
            with sqlite3.connect(REVIEWS_DB) as conn:
                conn.execute(
                    """
                    INSERT INTO reviews
                    (customer_name, rating, comment)
                    VALUES (?, ?, ?)
                    """,
                    (customer_name, rating, comment)
                )

            return redirect(
                url_for("location", submitted=1)
            )

    with sqlite3.connect(REVIEWS_DB) as conn:
        conn.row_factory = sqlite3.Row

        total_reviews = conn.execute(
            "SELECT COUNT(*) FROM reviews"
        ).fetchone()[0]

        average_rating = conn.execute(
            "SELECT AVG(rating) FROM reviews"
        ).fetchone()[0] or 0

        reviews = conn.execute(
            """
            SELECT customer_name, rating, comment, created_at
            FROM reviews
            ORDER BY id DESC
            """
        ).fetchall()

    return render_template(
        "location.html",
        average_rating=average_rating,
        total_reviews=total_reviews,
        reviews=reviews,
        submitted=request.args.get("submitted") == "1",
        error_message=error_message
    )


# --------------------------------------------------
# RAG CHATBOT
# --------------------------------------------------

@app.route("/chatbot", methods=["POST"])
def chatbot():
    data = request.get_json(silent=True) or {}
    question = data.get("message", "")

    if not isinstance(question, str):
        return jsonify({
            "reply": "Please enter a valid question."
        }), 400

    question = question.strip()

    if not question:
        return jsonify({
            "reply": "Please enter your question."
        }), 400

    try:
        answer = ask_menu(question)

        return jsonify({
            "reply": str(answer)
        })

    except Exception:
        app.logger.exception("Chatbot error")

        return jsonify({
            "reply": (
                "Sorry, the chatbot is temporarily unavailable."
            )
        }), 500


if __name__ == "__main__":
    app.run(debug=True)
