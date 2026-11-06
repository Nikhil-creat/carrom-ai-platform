location /ws/ {
  proxy_pass http://backend_api;
  proxy_http_version 1.1;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection $connection_upgrade;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_read_timeout 3600s;       # idle sockets are kept alive by the client's 5 s PING
  proxy_send_timeout 3600s;
  proxy_buffering off;
}
location /api/ {
  proxy_pass http://backend_api;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
  client_max_body_size 8m;        # camera frames for /api/vision/detect
}
location = /health { proxy_pass http://backend_api; }
location /metrics { return 404; }   # Prometheus scrapes backend:8000 directly on the internal network
location / {
  proxy_pass http://frontend_app;
  proxy_set_header Host $host;
}
