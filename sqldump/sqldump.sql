CREATE TABLE IF NOT EXISTS `charge_events` (
  `id` varchar(36) NOT NULL,
  `event_timestamp` timestamp NULL DEFAULT NULL,
  `event_type` text NOT NULL DEFAULT '(\'start\',\'stop\')',
  `range` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TRIGGER IF NOT EXISTS before_insert_uuid
BEFORE INSERT ON your_table
FOR EACH ROW
SET NEW.uuid_column = UUID();

CREATE TABLE IF NOT EXISTS `rawlogs` (
  `log_timestamp` timestamp NULL DEFAULT NULL,
  `log_message` text DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
