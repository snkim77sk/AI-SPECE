CREATE TABLE `collection_source_probes` (
	`id` text PRIMARY KEY NOT NULL,
	`session_id` text NOT NULL,
	`day` text NOT NULL,
	`page` integer NOT NULL,
	`failed_at` text NOT NULL,
	`attempt_owner` text NOT NULL,
	`status` text NOT NULL,
	`quota_day` text NOT NULL,
	`created_at` text NOT NULL,
	`completed_at` text,
	`result_json` text,
	FOREIGN KEY (`session_id`) REFERENCES `collection_sessions`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "collection_probe_status" CHECK("collection_source_probes"."status" IN ('reserved','complete'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `idx_collection_probe_snapshot` ON `collection_source_probes` (`session_id`,`day`,`page`,`failed_at`);