const express = require('express');
const { createServer } = require('http');
const { Server } = require('socket.io');
const axios = require('axios');
const cors = require('cors');
const amqp = require('amqplib');
require('dotenv').config();

const app = express();
app.use(cors());

const httpServer = createServer(app);
const io = new Server(httpServer, {
  cors: {
    origin: "*",
    methods: ["GET", "POST"]
  }
});

const PORT = process.env.PORT || 5050;
const DELIVERY_SERVICE_URL = process.env.DELIVERY_SERVICE_URL || 'http://delivery:5000';
const OSRM_URL = 'http://router.project-osrm.org/route/v1/driving/';
const RABBITMQ_URL = process.env.RABBITMQ_URL || 'amqp://guest:guest@rabbitmq:5672/';

let amqpChannel = null;
async function connectRabbitMQ() {
    try {
        const conn = await amqp.connect(RABBITMQ_URL);
        amqpChannel = await conn.createChannel();
        console.log('[Tracking] Connected to RabbitMQ');
    } catch (e) {
        console.error('[Tracking] Failed to connect to RabbitMQ, retrying in 5s...', e.message);
        setTimeout(connectRabbitMQ, 5000);
    }
}
connectRabbitMQ();

// Haversine formula (returns distance in meters)
function getDistance(lat1, lon1, lat2, lon2) {
  const p = 0.017453292519943295; // Math.PI / 180
  const c = Math.cos;
  const a = 0.5 - c((lat2 - lat1) * p)/2 + 
          c(lat1 * p) * c(lat2 * p) * 
          (1 - c((lon2 - lon1) * p))/2;
  return 12742 * Math.asin(Math.sqrt(a)) * 1000;
}

function publishNearDestination(context) {
    if (!amqpChannel) return;
    const msg = {
        event_type: 'rider_near_destination',
        orderID: context.orderID,
        patientID: context.patientID,
        patientName: context.patientName,
        subject: `MediConnect: Rider Arriving Soon for Order ${context.orderID}`,
        content: `Your rider is less than 500m away! Please get ready to receive your order.`,
        channel: 'both'
    };
    try {
        amqpChannel.publish(
            'service_exchange',
            'notification',
            Buffer.from(JSON.stringify(msg)),
            { persistent: true }
        );
        console.log(`[Tracking] Emitted rider_near_destination for ${context.deliveryID}`);
    } catch (e) {
        console.error('[Tracking] AMQP publish error:', e);
    }
}

// In-memory cache for delivery context (Patient Coords & Throttle)
const deliveryContext = new Map();

io.on('connection', (socket) => {
  console.log(`[Tracking] New connection: ${socket.id}`);

  socket.on('join_delivery', async (deliveryID) => {
    socket.join(deliveryID);
    console.log(`[Tracking] Socket ${socket.id} joined room: ${deliveryID}`);

    if (!deliveryContext.has(deliveryID)) {
      try {
        const response = await axios.get(`${DELIVERY_SERVICE_URL}/delivery/${deliveryID}`);
        const data = response.data.data;
        if (data.patientLat && data.patientLng) {
          deliveryContext.set(deliveryID, {
            deliveryID: deliveryID,
            orderID: data.orderID,
            patientID: data.patientID,
            patientName: data.patientName,
            patientLat: data.patientLat,
            patientLng: data.patientLng,
            lastEta: null,
            lastEtaTime: 0,
            nearDestinationSent: false
          });
        }
      } catch (error) {
        console.error(`[Tracking] Error fetching delivery ${deliveryID}:`, error.message);
      }
    }
  });

  socket.on('location_update', async (data) => {
    const { deliveryID, lat, lng } = data;
    if (!deliveryID || !lat || !lng) return;

    let context = deliveryContext.get(deliveryID);
    if (!context) return;

    context.riderLat = lat;
    context.riderLng = lng;

    const now = Date.now();
    const SHOULD_UPDATE_ETA = (now - context.lastEtaTime) > 10000;

    if (context.patientLat && context.patientLng) {
      // Check Geofence (if < 500m, emit notification)
      if (!context.nearDestinationSent) {
          const distanceMeters = getDistance(lat, lng, context.patientLat, context.patientLng);
          if (distanceMeters < 500) {
              publishNearDestination(context);
              context.nearDestinationSent = true;
          }
      }

      if (SHOULD_UPDATE_ETA) {
        try {
          const osrmCoords = `${lng},${lat};${context.patientLng},${context.patientLat}`;
          const osrmResponse = await axios.get(`${OSRM_URL}${osrmCoords}?overview=false`);
          
          if (osrmResponse.data.routes && osrmResponse.data.routes.length > 0) {
            const durationSeconds = osrmResponse.data.routes[0].duration;
            context.lastEta = Math.ceil(durationSeconds / 60); 
            context.lastEtaTime = now;
          }
        } catch (error) {
          console.error(`[Tracking] OSRM Error for ${deliveryID}:`, error.message);
        }
      }
    }

    io.to(deliveryID).emit('rider_location_update', {
      lat: context.riderLat,
      lng: context.riderLng,
      eta: context.lastEta,
      deliveryID: deliveryID
    });
  });

  socket.on('disconnect', () => {
    console.log(`[Tracking] Socket disconnected: ${socket.id}`);
  });
});

httpServer.listen(PORT, () => {
  console.log(`[Tracking] Service running on port ${PORT}`);
});
