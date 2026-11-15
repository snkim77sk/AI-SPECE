CREATE TABLE `server_collector_status` (
	`id` text PRIMARY KEY NOT NULL,
	`observed_at` text NOT NULL,
	`payload_json` text NOT NULL,
	`digest` text NOT NULL,
	`received_at` text NOT NULL
);
