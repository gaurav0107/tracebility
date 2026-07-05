{{/*
Expand the chart name (truncated to 63 chars per RFC 1123).
*/}}
{{- define "langprobe.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully-qualified release name: <release>-<chart>, capped at 63 chars.
*/}}
{{- define "langprobe.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "langprobe.api.fullname" -}}
{{- printf "%s-api" (include "langprobe.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "langprobe.ingestApi.fullname" -}}
{{- printf "%s-ingest-api" (include "langprobe.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "langprobe.ingestWorker.fullname" -}}
{{- printf "%s-ingest-worker" (include "langprobe.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "langprobe.scheduler.fullname" -}}
{{- printf "%s-scheduler" (include "langprobe.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "langprobe.web.fullname" -}}
{{- printf "%s-web" (include "langprobe.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "langprobe.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "langprobe.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Standard labels applied to every resource.
*/}}
{{- define "langprobe.labels" -}}
app.kubernetes.io/name: {{ include "langprobe.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "langprobe.api.selectorLabels" -}}
app.kubernetes.io/name: {{ include "langprobe.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: api
{{- end }}

{{- define "langprobe.ingestApi.selectorLabels" -}}
app.kubernetes.io/name: {{ include "langprobe.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: ingest-api
{{- end }}

{{- define "langprobe.ingestWorker.selectorLabels" -}}
app.kubernetes.io/name: {{ include "langprobe.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: ingest-worker
{{- end }}

{{- define "langprobe.scheduler.selectorLabels" -}}
app.kubernetes.io/name: {{ include "langprobe.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: scheduler
{{- end }}

{{- define "langprobe.web.selectorLabels" -}}
app.kubernetes.io/name: {{ include "langprobe.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: web
{{- end }}

{{/*
Image: prefer per-component tag when set; fall back to image.tag.
*/}}
{{- define "langprobe.image" -}}
{{- $reg := .root.Values.image.registry -}}
{{- $repo := .component.image.repository -}}
{{- $tag := default .root.Values.image.tag .component.image.tag -}}
{{- printf "%s/%s:%s" $reg $repo $tag }}
{{- end }}

{{/*
Resolve a credential. Renders:
  - secretKeyRef when existingSecret is set, OR
  - the literal `inline` value, OR
  - empty string (caller decides if that's fatal).
This indirection keeps the templates branch-free at the env-var site.

Note: this helper does NOT support `optional: true` on the secretKeyRef.
The OAuth env vars in api-deployment.yaml inline four secretKeyRef
blocks directly because they need `optional: true` (so a Secret with
only google_* keys doesn't crash the api when github_* is missing).
*/}}
{{- define "langprobe.envFromSecret" -}}
{{- $cfg := .cfg -}}
{{- $name := .name -}}
- name: {{ $name }}
  {{- if $cfg.existingSecret }}
  valueFrom:
    secretKeyRef:
      name: {{ $cfg.existingSecret }}
      key: {{ $cfg.existingSecretKey }}
  {{- else if $cfg.inlineDsn }}
  value: {{ $cfg.inlineDsn | quote }}
  {{- else if $cfg.inlineUrl }}
  value: {{ $cfg.inlineUrl | quote }}
  {{- else if $cfg.inlineSecret }}
  value: {{ $cfg.inlineSecret | quote }}
  {{- end }}
{{- end }}
