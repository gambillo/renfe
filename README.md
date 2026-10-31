# Vía Libre

Monitor ferroviario de España con frontend React/Vite y API Flask. El backend existente consulta y normaliza los datos de circulación; React se compila como recursos estáticos y Flask los sirve junto con `/api/trains` y `/health`.

## Desarrollo local

```sh
python -m pip install -r requirements.txt
python app.py
```

En otra terminal:

```sh
cd frontend
npm ci
npm run dev
```

Abre la URL que muestra Vite (normalmente `http://localhost:5173`). El proxy de desarrollo dirige `/api`, `/health` y `/static` a Flask en el puerto 8080.

## Docker

Docker construye React en una etapa Node y lo empaqueta con Flask/Gunicorn en una imagen de producción:

```sh
docker build -t via-libre .
docker run --name via-libre --restart unless-stopped -p 8080:8080 -e PORT=8080 via-libre
```

Abre `http://localhost:8080`. El contenedor escucha el puerto definido por `PORT` y expone la comprobación `GET /health`.

## Publicación pública

1. Sube el repositorio a GitHub.
2. En un proveedor compatible con Docker (por ejemplo, Render), crea un Web Service desde ese repositorio y selecciona el `Dockerfile` de la raíz.
3. Configura la comprobación de salud como `/health` y deja que el proveedor asigne `PORT`.
4. Para disponibilidad continua, elige una instancia siempre activa que no se suspenda por inactividad. Comprueba el precio del plan antes de confirmar el servicio.

La aplicación se sirve desde un único proceso web; no necesita una base de datos ni claves de mapas. Los datos en directo sí dependen de la disponibilidad de Radar de Trenes y de los feeds GTFS-RT de Renfe. Crear el servicio público, asignar un dominio o mantener una instancia activa requiere una cuenta del proveedor y no se realiza desde este repositorio.