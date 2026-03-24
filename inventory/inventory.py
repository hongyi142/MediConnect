from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# GET /inventory - Returns only the data inside the document
@app.route("/inventory", methods=['GET'])
def get_inventory():
    try:
        inventory_ref = db.collection('Inventory')
        docs = inventory_ref.stream()
        
        inventory_list = []
        for doc in docs:
            # We only take the dictionary data which already includes 'medicationID'
            item = doc.to_dict() 
            inventory_list.append(item)
            
        return jsonify({"code": 200, "data": inventory_list})
    except Exception as e:
        return jsonify({"code": 500, "message": str(e)}), 500
    
@app.route("/inventory/<string:medication_id>", methods=['GET'])
def get_one_item(medication_id):
    try:
        item_ref = db.collection('Inventory').document(medication_id)
        item = item_ref.get()

        if not item.exists:
            return jsonify({"code": 404, "message": "Medication not found"}), 404

        firestore_data = item.to_dict()
        # Force the key to match exactly what OutSystems expects
        return jsonify({
            "code": 200, 
            "data": {
                "Quantity": firestore_data.get('quantity', 0) # Match OutSystems Uppercase Q
            }
        }), 200
    except Exception as e:
        return jsonify({"code": 500, "message": str(e)}), 500

# POST /inventory - Now automatically sets medicationID to match the Document ID
@app.route("/inventory", methods=['POST'])
def add_new_medication():
    try:
        data = request.get_json()
        
        # Create the initial data block
        new_item = {
            "name": data.get('name'),
            "description": data.get('description'),
            "quantity": data.get('quantity'),
            "price": data.get('price')
        }
        
        if not all([new_item['name'], new_item['quantity'], new_item['price']]):
            return jsonify({"code": 400, "message": "Missing required fields."}), 400

        # 1. Add to Firebase to get the auto-generated ID
        update_time, doc_ref = db.collection('Inventory').add(new_item)
        
        # 2. Immediately update that same document to set medicationID = Document ID
        doc_ref.update({"medicationID": doc_ref.id})
        
        return jsonify({
            "code": 201, 
            "message": "New medication added.",
            "medicationID": doc_ref.id
        }), 201

    except Exception as e:
        return jsonify({"code": 500, "message": str(e)}), 500
    
# PUT /inventory/<medicationID> - Updates using medicationID
@app.route("/inventory/<string:medication_id>", methods=['PUT'])
def restock(medication_id):
    try:
        data = request.get_json()
        # We use medication_id from the URL to target the document
        item_ref = db.collection('Inventory').document(medication_id)
        item = item_ref.get()

        if not item.exists:
            return jsonify({"code": 404, "message": "Medication not found"}), 404

        update_data = {}
        if 'quantity' in data:
            current_stock = item.to_dict().get('quantity', 0)
            update_data['quantity'] = current_stock + data['quantity']
        
        if 'price' in data:
            update_data['price'] = data['price']

        item_ref.update(update_data)
        return jsonify({"code": 200, "message": "Inventory updated successfully"})
    except Exception as e:
        return jsonify({"code": 500, "message": str(e)}), 500
    
# PUT /inventory/<medicationID>/deduct - Deducts using medicationID
@app.route("/inventory/<string:medication_id>/deduct", methods=['PUT'])
def deduct_stock(medication_id):
    try:
        data = request.get_json()
        quantity_to_deduct = data.get('quantity')

        # Target the document using the medication_id passed from the Composite Service
        item_ref = db.collection('Inventory').document(medication_id)
        item = item_ref.get()

        if not item.exists:
            return jsonify({"code": 404, "message": "Medication not found."}), 404

        current_data = item.to_dict()
        current_stock = current_data.get('quantity', 0)

        if current_stock >= quantity_to_deduct:
            new_stock = current_stock - quantity_to_deduct
            item_ref.update({'quantity': new_stock})
            return jsonify({"code": 200, "message": "Stock deducted."}), 200
        
        return jsonify({"code": 400, "message": "Insufficient stock."}), 400
    except Exception as e:
        return jsonify({"code": 500, "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005, debug=True)