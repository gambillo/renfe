const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");
const STATIC_BASE = (import.meta.env.VITE_STATIC_BASE || API_BASE || "").replace(/\/$/, "");
const TRAIN_API_URL = API_BASE ? `${API_BASE}/api/trains` : "/api/trains";
const MAP_GEOMETRY_URL = STATIC_BASE ? `${STATIC_BASE}/static/map-geometry.json` : "/static/map-geometry.json";

function App() {
  const [trains, setTrains] = useState([]);
  const [geometry, setGeometry] = useState(null);
  const [updated, setUpdated] = useState(null);
  const [connection, setConnection] = useState("loading");
