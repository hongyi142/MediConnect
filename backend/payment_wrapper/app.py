import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import stripe

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
# Enable CORS for all domains on all routes
CORS(app)

# Set Stripe API key
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
if not stripe.api_key:
    raise ValueError("STRIPE_SECRET_KEY environment variable is not set. Please check your .env file.")

@app.route('/payment/checkout', methods=['POST'])
def create_checkout_session():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid or missing JSON payload"}), 400

        order_id = data.get('order_id')
        item_name = data.get('item_name')
        amount = data.get('amount')
        currency = data.get('currency', 'sgd')

        if order_id is None or item_name is None or amount is None:
            return jsonify({
                "error": "Missing required fields. Please provide 'order_id', 'item_name', and 'amount'."
            }), 400
            
        if amount <= 0:
            return jsonify({
                "error": "Invalid amount. Stripe requires the total amount to be greater than $0."
            }), 400

        # Create Stripe Checkout Session
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': currency,
                    'product_data': {
                        'name': item_name,
                    },
                    'unit_amount': amount,
                },
                'quantity': 1,
            }],
            mode='payment',
            client_reference_id=order_id,
            metadata={"order_id": order_id},
            success_url=f"{os.getenv('CLIENT_SUCCESS_URL')}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=os.getenv('CLIENT_CANCEL_URL'),
        )

        return jsonify({
            "checkout_url": session.url,
            "session_id": session.id
        }), 200

    except stripe.error.StripeError as e:
        return jsonify({
            "error": "A Stripe error occurred",
            "details": str(e)
        }), 400
    except Exception as e:
        return jsonify({
            "error": "An internal server error occurred",
            "details": str(e)
        }), 500

@app.route('/payment/status/<session_id>', methods=['GET'])
def get_payment_status(session_id):
    try:
        # Retrieve the session directly from Stripe
        session = stripe.checkout.Session.retrieve(session_id)

        return jsonify({
            "payment_status": session.payment_status,
            "order_id": session.client_reference_id
        }), 200

    except stripe.error.StripeError as e:
        return jsonify({
            "error": "A Stripe error occurred while retrieving the session",
            "details": str(e)
        }), 400
    except Exception as e:
        return jsonify({
            "error": "An internal server error occurred",
            "details": str(e)
        }), 500

@app.route('/payment/refund', methods=['POST'])
def process_refund():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid or missing JSON payload"}), 400

        session_id = data.get('session_id')
        if not session_id:
            return jsonify({"error": "Missing required field: 'session_id'."}), 400

        # Retrieve the session to get the payment intent
        session = stripe.checkout.Session.retrieve(session_id)
        payment_intent = session.payment_intent

        if not payment_intent:
            return jsonify({"error": "No payment intent found for this session."}), 400

        # Create the refund
        stripe.Refund.create(payment_intent=payment_intent)

        return jsonify({
            "message": "Refund processed successfully"
        }), 200

    except stripe.error.StripeError as e:
        return jsonify({
            "error": "A Stripe error occurred while processing the refund",
            "details": str(e)
        }), 400
    except Exception as e:
        return jsonify({
            "error": "An internal server error occurred",
            "details": str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
