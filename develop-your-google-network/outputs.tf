output "sql_connection_name" {
  description = "Connection name for Cloud SQL database instance"
  value       = google_sql_database_instance.wp-dev-db.connection_name
}
