#!/bin/sh
# Generate alertmanager.yml from template with environment variables

cat > /etc/alertmanager/alertmanager.yml << EOF
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname', 'severity']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  receiver: 'telegram'
  routes:
    - match:
        severity: critical
      receiver: 'telegram-critical'
      continue: true
    - match:
        severity: warning
      receiver: 'telegram-warning'
      repeat_interval: 3h

receivers:
  - name: 'telegram'
    telegram_configs:
      - bot_token: '${ALERT_BOT_TOKEN}'
        chat_id: ${ADMIN_CHAT_ID}
        message: |
          {{ range .Alerts }}
          ⚠️ *{{ .Annotations.summary }}*

          {{ .Annotations.description }}

          Severity: {{ .Labels.severity }}
          {{ end }}
        parse_mode: 'Markdown'

  - name: 'telegram-critical'
    telegram_configs:
      - bot_token: '${ALERT_BOT_TOKEN}'
        chat_id: ${ADMIN_CHAT_ID}
        message: |
          🚨 *КРИТИЧЕСКАЯ ОШИБКА*

          {{ range .Alerts }}
          *{{ .Annotations.summary }}*

          {{ .Annotations.description }}

          Время: {{ .StartsAt }}
          {{ end }}
        parse_mode: 'Markdown'

  - name: 'telegram-warning'
    telegram_configs:
      - bot_token: '${ALERT_BOT_TOKEN}'
        chat_id: ${ADMIN_CHAT_ID}
        message: |
          ⚡ *Предупреждение*

          {{ range .Alerts }}
          *{{ .Annotations.summary }}*

          {{ .Annotations.description }}
          {{ end }}
        parse_mode: 'Markdown'

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname']
EOF

# Start alertmanager
exec /bin/alertmanager \
  --config.file=/etc/alertmanager/alertmanager.yml \
  --storage.path=/alertmanager \
  --web.external-url=http://localhost:9093
