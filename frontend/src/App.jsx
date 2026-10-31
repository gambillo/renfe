import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowDownToLine,
  Clock3,
  MapPin,
  RefreshCw,
  Search,
  Train,
  X
} from "lucide-react";

const MAP_WIDTH = 1100;
const MAP_HEIGHT = 950;
const REFRESH_MS = 30_000;
const TRAIN_BATCH_SIZE = 12;
const MOBILE_BREAKPOINT = 860;

const RAIL_LINES = [
  [[43.36, -8.4], [42.88, -8.54], [42.34, -7.86], [41.5, -5.7], [41.65, -4.73], [40.42, -3.7]],
  [[42.14, -8.62], [41.55, -8.42], [41.16, -7.8], [40.64, -8.65], [40.2, -7.9], [40.42, -3.7]],
  [[40.42, -3.7], [41.65, -4.73], [42.34, -3.7], [43.26, -2.93], [43.32, -1.98]],
  [[40.42, -3.7], [41.65, -0.88], [41.39, 2.17]],
  [[40.42, -3.7], [39.57, 2.65], [41.39, 2.17]],
  [[40.42, -3.7], [39.86, -4.02], [39.0, -1.86], [39.47, -0.38], [39.57, 2.65]],
  [[40.42, -3.7], [38.99, -1.13], [38.35, -0.49]],
  [[40.42, -3.7], [38.0, -4.8], [37.89, -4.78], [37.39, -5.99], [36.53, -6.29]],
  [[37.89, -4.78], [36.72, -4.42]],
  [[41.65, -4.73], [42.14, -0.41], [41.65, -0.88]],
  [[39.47, -0.38], [38.35, -0.49], [38.0, -4.8]]
];

const CITIES = [
  [43.36, -8.4, "A Coruña", -8, -10], [42.88, -8.54, "Santiago", -28, 18],
  [43.26, -2.93, "Bilbao", 8, -8], [43.32, -1.98, "San Sebastián", 8, 14],
  [43.36, -5.85, "Oviedo", 8, -8], [42.34, -3.7, "Burgos", 8, -8],
  [41.65, -4.73, "Valladolid", 8, -9], [40.42, -3.7, "Madrid", 9, -10],
  [41.65, -0.88, "Zaragoza", 8, -8], [41.39, 2.17, "Barcelona", 8, -9],
  [39.47, -0.38, "Valencia", 8, -8], [38.35, -0.49, "Alicante", 8, 16],
  [37.89, -4.78, "Córdoba", 8, -8], [37.39, -5.99, "Sevilla", 8, 16],
  [36.72, -4.42, "Málaga", 8, 16], [36.53, -6.29, "Cádiz", -35, 20]
];

function projectPoint(latitude, longitude) {
  return { x: (longitude + 11) * 68, y: (45.5 - latitude) * 88 };
}

function makeRoutePath(points) {
  return points.map(([latitude, longitude], index) => {
    const point = projectPoint(latitude, longitude);
    return `${index ? "L" : "M"}${point.x.toFixed(1)},${point.y.toFixed(1)}`;
  }).join("");
}

function formatTime(value) {
  if (value === undefined || value === null || value === "") return "—";
  const date = /^\d{10,13}$/.test(String(value))
    ? new Date(Number(value) * (String(value).length === 10 ? 1000 : 1))
    : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("es-ES", {
    timeZone: "Europe/Madrid",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23"
  }).format(date);
}

function estimatedArrival(train) {
  return train.expectedArrival;
}

function scheduledArrival(train) {
  const estimate = estimatedArrival(train);
  if (!estimate || typeof train.delayMinutes !== "number") return null;
  const timestamp = new Date(estimate).getTime();
  if (!Number.isFinite(timestamp)) return null;
  return new Date(timestamp - train.delayMinutes * 60_000).toISOString();
}

function serviceName(train) {
  const raw = [train.productName, train.productCategory, train.providerProduct, train.serviceName,
    train.trafficType, train.type, train.category, train.transportType]
    .filter(Boolean).join(" ").toLocaleUpperCase("es");
  const normalized = raw.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  if (/\bAVLO\b/.test(normalized)) return "Avlo";
  if (/\bAVE\b/.test(normalized)) return "AVE";
  if (/\bAVANT\b/.test(normalized)) return "Avant";
  if (/\bALVIA\b/.test(normalized)) return "Alvia";
  if (/\bINTERCITY\b/.test(normalized)) return "Intercity";
  if (/\bEUROMED\b/.test(normalized)) return "Euromed";
  if (/CERCAN|RODAL/.test(normalized)) return "Cercanías";
  if (/MEDIA DISTANCIA|REGIONAL|\bMD\b/.test(normalized)) return "Media Distancia";
  return train.transportType || train.productName || "Tipo sin identificar";
}

function delayColor(delay) {
  if (typeof delay !== "number") return "#76b7d4";
  if (delay >= 15) return "#f06b62";
  if (delay > 0) return "#f0b454";
  return "#52d39a";
}

function delayLabel(delay) {
  if (typeof delay !== "number") return "Sin dato";
  return `${delay > 0 ? "+" : ""}${Math.round(delay)} min`;
}

function trainId(train) {
  return String(train.id ?? train.trainCode ?? "");
}

function kilometerPoint(train) {
  const point = train.km ?? train.kilometer ?? train.pk;
  return point === "" ? null : point ?? null;
}

function getCommunities(train) {
  if (Array.isArray(train.autonomousCommunities)) return train.autonomousCommunities;
  return train.autonomousCommunity || train.community ? [train.autonomousCommunity || train.community] : [];
}

function App() {
  const [trains, setTrains] = useState([]);
  const [geometry, setGeometry] = useState(null);
  const [updated, setUpdated] = useState(null);
  const [connection, setConnection] = useState("loading");
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [serviceFilter, setServiceFilter] = useState("");
  const [communityFilter, setCommunityFilter] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [visibleLimit, setVisibleLimit] = useState(TRAIN_BATCH_SIZE);
  const [mobileView, setMobileView] = useState("map");
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.innerWidth <= MOBILE_BREAKPOINT;
  });

  const trainListRef = useRef(null);
  const loadMoreRef = useRef(null);

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth <= MOBILE_BREAKPOINT);
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    if (!isMobile) setMobileView("map");
  }, [isMobile]);

  useEffect(() => {
    let cancelled = false;
    async function loadGeometry() {
      try {
        const response = await fetch("/static/map-geometry.json");
        if (!response.ok) throw new Error("No se pudo cargar la geometría del mapa");
        const data = await response.json();
        if (!cancelled) setGeometry(data);
      } catch (loadError) {
        if (!cancelled) setError(loadError.message);
      }
    }
    loadGeometry();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadTrains() {
      setRefreshing(true);
      try {
        const response = await fetch("/api/trains", { cache: "no-store" });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || "No se pudieron cargar los trenes");
        if (cancelled) return;
        setTrains(Array.isArray(data.trains) ? data.trains.filter((train) => serviceName(train) !== "Cercanías") : []);
        setUpdated(data.updated);
        setConnection("online");
        setError("");
      } catch (loadError) {
        if (cancelled) return;
        setConnection("offline");
        setError(loadError.message || "No se pudieron cargar los trenes");
      } finally {
        if (!cancelled) setRefreshing(false);
      }
    }
    loadTrains();
    const timer = window.setInterval(loadTrains, REFRESH_MS);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    setVisibleLimit(TRAIN_BATCH_SIZE);
  }, [search, serviceFilter, communityFilter]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape" && selectedId) {
        setSelectedId(null);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedId]);

  const services = [...new Set(trains.map(serviceName))].filter(Boolean).sort((a, b) => a.localeCompare(b, "es"));
  const communities = [...new Set(trains.flatMap(getCommunities))].filter(Boolean).sort((a, b) => a.localeCompare(b, "es"));
  const normalizedSearch = search.trim().toLocaleLowerCase("es");

  const visibleTrains = trains.filter((train) => {
    const searchable = [train.trainCode, serviceName(train), train.originName, train.destinationName,
      train.nextStationName, train.currentLocation, ...getCommunities(train)].join(" ").toLocaleLowerCase("es");
    return searchable.includes(normalizedSearch)
      && (!serviceFilter || serviceName(train) === serviceFilter)
      && (!communityFilter || getCommunities(train).includes(communityFilter));
  }).sort((a, b) => (b.delayMinutes ?? -Infinity) - (a.delayMinutes ?? -Infinity));

  const renderedTrains = visibleTrains.slice(0, visibleLimit);
  const mapTrains = trains.filter((train) => typeof train.latitude === "number" && typeof train.longitude === "number");

  const delayedTrains = trains.filter((train) => typeof train.delayMinutes === "number" && train.delayMinutes > 0);
  const delays = trains.map((train) => train.delayMinutes).filter((delay) => typeof delay === "number");
  const averageDelay = delays.length ? Math.round(delays.reduce((sum, delay) => sum + delay, 0) / delays.length) : null;
  const selectedTrain = trains.find((train) => trainId(train) === selectedId);

  useEffect(() => {
    if (selectedId && !visibleTrains.some((train) => trainId(train) === selectedId)) {
      setSelectedId(null);
    }
  }, [selectedId, visibleTrains]);

  useEffect(() => {
    const rootNode = trainListRef.current;
    const sentinel = loadMoreRef.current;
    if (!rootNode || !sentinel) return undefined;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setVisibleLimit((current) => Math.min(current + TRAIN_BATCH_SIZE, visibleTrains.length));
        }
      },
      {
        root: rootNode,
        rootMargin: "200px 0px",
        threshold: 0.1
      }
    );

    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [visibleTrains.length]);

  function focusTrain(train) {
    setSelectedId(trainId(train));
    if (isMobile) setMobileView("map");
  }

  function closeTrainInfo() {
    setSelectedId(null);
  }

  function exportCsv() {
    const fields = ["trainCode", "service", "originName", "destinationName", "delayMinutes", "scheduledArrival", "estimatedArrival", "latitude", "longitude"];
    const escape = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
    const rows = [fields.join(";"), ...visibleTrains.map((train) => fields.map((field) => {
      if (field === "service") return escape(serviceName(train));
      if (field === "scheduledArrival") return escape(scheduledArrival(train));
      if (field === "estimatedArrival") return escape(estimatedArrival(train));
      return escape(train[field]);
    }).join(";"))];
    const blob = new Blob(["\uFEFF" + rows.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "via-libre-trenes.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Red Ferroviaria Española, inicio">
          <span className="brand-icon"><Train size={19} strokeWidth={2.2} /></span>
          <span className="brand-copy"><strong>Red Ferroviaria Española</strong><small>Monitor ferroviario de España</small></span>
        </a>
        <div className={`connection connection-${connection}`}>
          <span className="connection-dot" />
          <span>{connection === "online" ? "DATOS EN DIRECTO" : connection === "offline" ? "SIN CONEXIÓN" : "CONECTANDO"}</span>
          {updated && <time dateTime={updated}>Actualizado {formatTime(updated)}</time>}
        </div>
      </header>

      {isMobile && (
        <div className="mobile-tabs" aria-label="Selector de vista móvil">
          <button
            type="button"
            className={mobileView === "map" ? "mobile-tab active" : "mobile-tab"}
            onClick={() => setMobileView("map")}
          >
            Mapa
          </button>
          <button
            type="button"
            className={mobileView === "trains" ? "mobile-tab active" : "mobile-tab"}
            onClick={() => setMobileView("trains")}
          >
            Viajes
          </button>
        </div>
      )}

      <div className={`dashboard ${isMobile ? `mobile-view-${mobileView}` : ""}`}>
        <section className="map-section" aria-label="Mapa de trenes en circulación">
          <div className="map-heading">
            <div><span className="eyebrow">RED FERROVIARIA</span><h1>España, en movimiento</h1></div>
          </div>

          <div className="map-frame">
            <svg
              className="rail-map"
              viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
              preserveAspectRatio="xMidYMid meet"
              role="img"
              aria-label="Mapa vectorial de España con ciudades, red ferroviaria y trenes en circulación"
            >
              <defs>
                <pattern id="map-grid" width="56" height="56" patternUnits="userSpaceOnUse">
                  <path d="M 56 0 L 0 0 0 56" fill="none" stroke="#18303a" strokeWidth="1" />
                </pattern>
              </defs>
              <rect x="0" y="0" width={MAP_WIDTH} height={MAP_HEIGHT} fill="#0a171d" />
              <rect x="0" y="0" width={MAP_WIDTH} height={MAP_HEIGHT} fill="url(#map-grid)" />
              {geometry?.neighbors.map((path, index) => <path key={`neighbor-${index}`} d={path} className="land land-neighbor" />)}
              {geometry?.spain.map((path, index) => <path key={`spain-${index}`} d={path} className="land land-spain" />)}
              <rect x="34" y="812" width="220" height="105" rx="4" className="islands-frame" />
              {geometry?.canaries.map((path, index) => <path key={`canary-${index}`} d={path} className="land land-spain" />)}
              {RAIL_LINES.map((line, index) => <path key={`route-${index}`} d={makeRoutePath(line)} className="rail-line" />)}
              {CITIES.map(([latitude, longitude, name, dx, dy]) => {
                const point = projectPoint(latitude, longitude);
                return <g key={name} className="city-label"><circle cx={point.x} cy={point.y} r="3.4" /><text x={point.x + dx} y={point.y + dy}>{name}</text></g>;
              })}
              <text x="144" y="904" textAnchor="middle" className="islands-label">Islas Canarias</text>
              {mapTrains.map((train) => {
                const point = projectPoint(train.latitude, train.longitude);
                const color = delayColor(train.delayMinutes);
                const active = trainId(train) === selectedId;
                return (
                  <g
                    key={trainId(train)}
                    className={`train-point${active ? " train-point-active" : ""}`}
                    data-train-marker="true"
                    transform={`translate(${point.x} ${point.y})`}
                    role="button"
                    tabIndex="0"
                    aria-label={`${serviceName(train)} ${train.trainCode || ""}, ${train.originName || "origen"} a ${train.destinationName || "destino"}`}
                    onClick={() => focusTrain(train)}
                    onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") focusTrain(train); }}
                  >
                    <title>{`${serviceName(train)} ${train.trainCode || ""} · ${train.originName || "Origen"} → ${train.destinationName || "Destino"}`}</title>
                    <circle r="15" className="train-point-halo" fill={color} />
                    <circle r="7" className="train-point-core" fill={color} />
                  </g>
                );
              })}
            </svg>

            <div className="map-legend" aria-label="Leyenda de retrasos">
              <span><i className="legend-dot on-time" /> En hora</span>
              <span><i className="legend-dot slight-delay" /> Con retraso</span>
              <span><i className="legend-dot major-delay" /> +15 min</span>
            </div>
            <div className="map-counter"><MapPin size={14} /> {mapTrains.length} trenes en mapa</div>
            {selectedTrain && (
              <section className="selected-train" aria-label="Detalle del tren seleccionado">
                <button className="close-selected" type="button" onClick={closeTrainInfo} aria-label="Cerrar detalle"><X size={15} /></button>
                <span className="eyebrow">{serviceName(selectedTrain)}</span>
                <strong>{selectedTrain.trainCode || "Tren"}</strong>
                <span>{selectedTrain.originName || "Origen"} → {selectedTrain.destinationName || "Destino"}</span>
                <span>Parada: {selectedTrain.currentStationName || selectedTrain.nextStationName || selectedTrain.currentLocation || "Ubicación no disponible"}</span>
                <span>Velocidad: {typeof selectedTrain.speedKmh === "number" ? `${Math.round(selectedTrain.speedKmh)} km/h${selectedTrain.speedSource === "estimated" ? " · estimada" : ""}` : "Sin dato"}</span>
                {kilometerPoint(selectedTrain) !== null && <span>Punto km: {kilometerPoint(selectedTrain)}</span>}
                <span className="selected-delay" style={{ color: delayColor(selectedTrain.delayMinutes) }}>{delayLabel(selectedTrain.delayMinutes)}</span>
              </section>
            )}
          </div>
          <p className="map-attribution">Cartografía Natural Earth · dominio público · red ferroviaria esquemática</p>
        </section>

        <aside className="traffic-panel" aria-label="Tráfico ferroviario">
          <div className="panel-heading">
            <div><span className="eyebrow">CIRCULACIÓN</span><h2>Trenes en servicio</h2></div>
            <button type="button" className="icon-button refresh-button" onClick={() => window.location.reload()} aria-label="Actualizar datos" title="Actualizar datos"><RefreshCw size={17} className={refreshing ? "spinning" : ""} /></button>
          </div>

          <div className="stats-row">
            <div className="stat"><span>Detectados</span><strong>{trains.length}</strong></div>
            <div className="stat"><span>Con retraso</span><strong className="stat-late">{delayedTrains.length}</strong></div>
            <div className="stat"><span>Media</span><strong>{averageDelay === null ? "—" : `${averageDelay}′`}</strong></div>
          </div>

          <div className="filter-stack">
            <label className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar tren, ciudad…" aria-label="Buscar tren, ciudad" /></label>
            <div className="select-row">
              <label><span>Servicio</span><select value={serviceFilter} onChange={(event) => setServiceFilter(event.target.value)}><option value="">Todos</option>{services.map((service) => <option key={service} value={service}>{service}</option>)}</select></label>
              <label><span>Comunidad</span><select value={communityFilter} onChange={(event) => setCommunityFilter(event.target.value)}><option value="">Todas</option>{communities.map((community) => <option key={community} value={community}>{community}</option>)}</select></label>
            </div>
          </div>

          <div className="list-toolbar"><h3>Circulaciones</h3><span>{visibleTrains.length}</span></div>
          <div className="train-list" ref={trainListRef} aria-live="polite">
            {connection === "loading" && <div className="empty-state">Conectando con las fuentes ferroviarias…</div>}
            {connection === "offline" && <div className="empty-state error-state"><AlertTriangle size={17} /><span>{error || "No se pudo cargar la circulación."}</span></div>}
            {connection !== "loading" && connection !== "offline" && visibleTrains.length === 0 && <div className="empty-state">No hay trenes que coincidan con los filtros.</div>}
            {renderedTrains.map((train) => (
              <button type="button" className={`train-row${trainId(train) === selectedId ? " train-row-active" : ""}`} key={trainId(train)} onClick={() => focusTrain(train)}>
                <div className="train-row-top"><div><strong>{serviceName(train)}</strong><span>{train.trainCode || "Sin número"}</span></div><b className="delay-badge" style={{ color: delayColor(train.delayMinutes), borderColor: delayColor(train.delayMinutes), background: `${delayColor(train.delayMinutes)}22` }}>{delayLabel(train.delayMinutes)}</b></div>
                <div className="train-route">{train.originName || "Origen sin dato"}<span>→</span>{train.destinationName || "Destino sin dato"}</div>
                <div className="train-status">
                  <span><small>Parada / ubicación</small><b>{train.currentStationName || train.nextStationName || train.currentLocation || "Ubicación no disponible"}</b></span>
                  <span><small>Velocidad</small><b>{typeof train.speedKmh === "number" ? `${Math.round(train.speedKmh)} km/h${train.speedSource === "estimated" ? " · estimada" : ""}` : "Sin dato"}</b></span>
                  {kilometerPoint(train) !== null && <span><small>Punto km</small><b>{kilometerPoint(train)}</b></span>}
                </div>
                <div className="arrival-pair"><span><small>Programada</small><b>{formatTime(scheduledArrival(train))}</b></span><span><small>Estimada</small><b>{formatTime(estimatedArrival(train))}</b></span></div>
              </button>
            ))}
            {visibleTrains.length > renderedTrains.length && (
              <button ref={loadMoreRef} type="button" className="load-more" onClick={() => setVisibleLimit((current) => Math.min(current + TRAIN_BATCH_SIZE, visibleTrains.length))}>
                Cargar más trenes
              </button>
            )}
          </div>

          <div className="panel-footer">
            <span><Clock3 size={13} /> Actualización cada 30 s</span>
            <button type="button" onClick={exportCsv} disabled={!visibleTrains.length}><ArrowDownToLine size={14} /> CSV</button>
          </div>
          {error && connection === "online" && <p className="subtle-warning">{error}</p>}
        </aside>
      </div>
    </main>
  );
}

export default App;
