import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore

# Load environment variables
load_dotenv()

# Retrieve Firebase credentials path
cred_path = os.getenv("FIREBASE_CRED_PATH")
if cred_path is None:
    raise ValueError("FIREBASE_CRED_PATH environment variable is not set. Please check your .env file.")

# Initialize the Firebase Admin SDK
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)

# Initialize the Firestore client
db = firestore.client()

# Set up the Flask application
app = Flask(__name__)

# Create payment record
@app.route('/payment/create', methods=['POST'])
def create_payment():
    try:
        # Extract JSON data from the request
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid or missing JSON payload"}), 400

        order_id = data.get('orderID')
        stripe_intent_id = data.get('stripeIntentID')
        amount = data.get('amount')

        # Input validation: Check if all three fields are present
        if order_id is None or stripe_intent_id is None or amount is None:
            return jsonify({
                "error": "Missing required fields. Please provide 'orderID', 'stripeIntentID', and 'amount'."
            }), 400

        # Prepare the document to be inserted
        payment_data = {
            "orderID": order_id,
            "stripeIntentID": stripe_intent_id,
            "amount": amount,
            "status": "pending"
        }

        # Write the new document into the 'Payment' collection in Firestore
        # .add() returns a tuple: (update_time, document_reference)
        _, doc_ref = db.collection('Payment').add(payment_data)

        # Return success response with the new Document ID
        return jsonify({
            "message": "Payment record created successfully",
            "documentID": doc_ref.id
        }), 201

    except Exception as e:
        # Catch database write failures or any other unexpected errors
        return jsonify({
            "error": "An error occurred while creating the payment record",
            "details": str(e)
        }), 500

# Update payment status
@app.route('/payment/<paymentID>', methods=['PUT'])
def update_payment(paymentID):
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid or missing JSON payload"}), 400

        status = data.get('status')
        if not status:
            return jsonify({"error": "Missing required field: 'status'."}), 400

        valid_statuses = ['pending', 'paid', 'failed', 'refunded']
        if status not in valid_statuses:
            return jsonify({
                "error": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
            }), 400

        # Retrieve the document from Firestore
        payment_ref = db.collection('Payment').document(paymentID)
        doc = payment_ref.get()

        if not doc.exists:
            return jsonify({"error": "Payment record not found."}), 404

        # Update the status field
        payment_ref.update({"status": status})

        return jsonify({
            "message": "Payment status updated successfully",
            "documentID": paymentID,
            "status": status
        }), 200

    except Exception as e:
        return jsonify({
            "error": "An error occurred while updating the payment record",
            "details": str(e)
        }), 500

# Get payment status
@app.route('/payment/<paymentID>', methods=['GET'])
def get_payment(paymentID):
    try:
        # Retrieve the document from Firestore
        payment_ref = db.collection('Payment').document(paymentID)
        doc = payment_ref.get()

        if not doc.exists:
            return jsonify({"error": "Payment record not found."}), 404

        # Retrieve only the status field
        payment_status = doc.to_dict().get('status')
        
        return jsonify({
            "documentID": paymentID,
            "status": payment_status
        }), 200

    except Exception as e:
        return jsonify({
            "error": "An error occurred while fetching the payment record",
            "details": str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
