CREATE TABLE `dataset_epochs` (
	`mode` text PRIMARY KEY NOT NULL,
	`revision` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `query_cache` (
	`cache_key` text PRIMARY KEY NOT NULL,
	`mode` text NOT NULL,
	`revision` integer NOT NULL,
	`payload` text NOT NULL,
	`created_at` integer NOT NULL,
	`expires_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_query_cache_mode_time` ON `query_cache` (`mode`,`created_at`);--> statement-breakpoint
INSERT INTO dataset_epochs(mode,revision) VALUES('live',0),('synthetic',0);
