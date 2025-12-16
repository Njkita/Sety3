#!/usr/bin/env bash
set -euo pipefail

CERT_DIR="/mesh/certs"
CFG_DIR="/mesh/configs"

mkdir -p "$CERT_DIR" "$CFG_DIR"

gen_ca() {
  if [[ -f "$CERT_DIR/ca.crt" && -f "$CERT_DIR/ca.key" ]]; then
    echo "[meshctl] CA exists, skip"
    return
  fi

  echo "[meshctl] Generating CA..."
  openssl genrsa -out "$CERT_DIR/ca.key" 4096
  openssl req -x509 -new -nodes -key "$CERT_DIR/ca.key" \
    -sha256 -days 3650 \
    -subj "/CN=notes-mesh-ca" \
    -out "$CERT_DIR/ca.crt"
}

gen_cert() {
  local name="$1"
  local cn="$1"

  if [[ -f "$CERT_DIR/${name}.crt" && -f "$CERT_DIR/${name}.key" ]]; then
    echo "[meshctl] cert ${name} exists, skip"
    return
  fi

  echo "[meshctl] Generating cert for ${name}..."
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$CERT_DIR/${name}.key"


  openssl req -new -key "$CERT_DIR/${name}.key" \
    -subj "/CN=${cn}" \
    -out "$CERT_DIR/${name}.csr"

  cat > "$CERT_DIR/${name}.ext" <<EOF
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=DNS:${name}
EOF

  openssl x509 -req -in "$CERT_DIR/${name}.csr" \
    -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
    -out "$CERT_DIR/${name}.crt" \
    -days 365 -sha256 \
    -extfile "$CERT_DIR/${name}.ext"

  rm -f "$CERT_DIR/${name}.csr" "$CERT_DIR/${name}.ext"
}

write_envoy_notes() {
  local svc="$1"   # service1 / service2

  cat > "$CFG_DIR/${svc}.yaml" <<EOF
static_resources:
  listeners:
  - name: listener_https_notes
    address:
      socket_address: { address: 0.0.0.0, port_value: 9443 }
    filter_chains:
    - transport_socket:
        name: envoy.transport_sockets.tls
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext
          require_client_certificate: true
          common_tls_context:
            tls_certificates:
            - certificate_chain: { filename: /etc/mesh/certs/${svc}.crt }
              private_key: { filename: /etc/mesh/certs/${svc}.key }
            validation_context:
              trusted_ca: { filename: /etc/mesh/certs/ca.crt }
      filters:
      - name: envoy.filters.network.http_connection_manager
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          stat_prefix: ingress_http
          codec_type: AUTO
          route_config:
            name: local_route
            virtual_hosts:
            - name: local
              domains: ["*"]
              routes:
              - match: { prefix: "/" }
                route: { cluster: local_notes_http }
          http_filters:
          - name: envoy.filters.http.router
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router

  - name: listener_grpc_tls
    address:
      socket_address: { address: 0.0.0.0, port_value: 9444 }
    filter_chains:
    - transport_socket:
        name: envoy.transport_sockets.tls
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext
          require_client_certificate: true
          common_tls_context:
            alpn_protocols: ["h2"]
            tls_certificates:
            - certificate_chain: { filename: /etc/mesh/certs/${svc}.crt }
              private_key: { filename: /etc/mesh/certs/${svc}.key }
            validation_context:
              trusted_ca: { filename: /etc/mesh/certs/ca.crt }
      filters:
      - name: envoy.filters.network.http_connection_manager
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          stat_prefix: ingress_grpc
          codec_type: AUTO
          http2_protocol_options: {}
          route_config:
            name: grpc_route
            virtual_hosts:
            - name: grpc
              domains: ["*"]
              routes:
              - match: { prefix: "/" }
                route: { cluster: local_notes_grpc }
          http_filters:
          - name: envoy.filters.http.router
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router

  clusters:
  - name: local_notes_http
    type: STATIC
    connect_timeout: 1s
    lb_policy: ROUND_ROBIN
    load_assignment:
      cluster_name: local_notes_http
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address: { address: 127.0.0.1, port_value: 8000 }

  - name: local_notes_grpc
    type: STATIC
    connect_timeout: 1s
    lb_policy: ROUND_ROBIN
    http2_protocol_options: {}
    load_assignment:
      cluster_name: local_notes_grpc
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address: { address: 127.0.0.1, port_value: 50051 }

admin:
  address:
    socket_address: { address: 0.0.0.0, port_value: 9901 }
EOF
}

write_envoy_mailer() {
  cat > "$CFG_DIR/mailer.yaml" <<EOF
static_resources:
  listeners:
  - name: listener_https_mailer
    address:
      socket_address: { address: 0.0.0.0, port_value: 9443 }
    filter_chains:
    - transport_socket:
        name: envoy.transport_sockets.tls
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext
          require_client_certificate: true
          common_tls_context:
            tls_certificates:
            - certificate_chain: { filename: /etc/mesh/certs/mailer.crt }
              private_key: { filename: /etc/mesh/certs/mailer.key }
            validation_context:
              trusted_ca: { filename: /etc/mesh/certs/ca.crt }
      filters:
      - name: envoy.filters.network.http_connection_manager
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          stat_prefix: ingress_mailer
          codec_type: AUTO
          route_config:
            name: mailer_route
            virtual_hosts:
            - name: mailer
              domains: ["*"]
              routes:
              - match: { prefix: "/" }
                route: { cluster: local_mailer_http }
          http_filters:
          - name: envoy.filters.http.router
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router

  clusters:
  - name: local_mailer_http
    type: STATIC
    connect_timeout: 1s
    lb_policy: ROUND_ROBIN
    load_assignment:
      cluster_name: local_mailer_http
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address: { address: 127.0.0.1, port_value: 8000 }

admin:
  address:
    socket_address: { address: 0.0.0.0, port_value: 9902 }
EOF
}

main() {
  gen_ca

  gen_cert "service1"
  gen_cert "service2"
  gen_cert "mailer"
  gen_cert "lb"

  write_envoy_notes "service1"
  write_envoy_notes "service2"
  write_envoy_mailer

  find "$CERT_DIR" -maxdepth 1 -type f -name "*.crt" -exec chmod 644 {} \;
  find "$CERT_DIR" -maxdepth 1 -type f -name "*.key" ! -name "ca.key" -exec chmod 644 {} \;
  chmod 755 "$CERT_DIR" "$CFG_DIR"

  touch /mesh/READY
  echo "[meshctl] READY"
  tail -f /dev/null
}

main
