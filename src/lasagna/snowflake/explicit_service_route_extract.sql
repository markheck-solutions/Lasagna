----------------------------------------------------------------------
-- Lasagna explicit IC/ICB service seed
-- Source: Spaghetti sql/sdm_inca_workspace_pm.sql product inventory segment
-- Source commit: d5871b1e17c8772ae7836b158b1a1ddd9e4566fd
-- No PM assigned-order seed, workpack, geo, Salesforce queue, or UI lanes.
----------------------------------------------------------------------
USE SCHEMA prod_access_db.inca_src;

CREATE OR REPLACE TEMP TABLE prod_service_seed_rows AS
SELECT UPPER(column1::VARCHAR) AS service_id, 'manual_input' AS source_type
FROM VALUES
/* LASAGNA_SERVICE_VALUES */
;

CREATE OR REPLACE TEMPORARY TABLE prod_services (service_id VARCHAR);
CREATE OR REPLACE TEMPORARY TABLE prod_bearers (qid VARCHAR, row_data VARIANT);
CREATE OR REPLACE TEMPORARY TABLE prod_edges (level_tag VARCHAR, service_id VARCHAR, edge_name VARCHAR);
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.
CREATE OR REPLACE TEMPORARY TABLE prod_all (qid VARCHAR, row_data VARIANT);

INSERT INTO prod_services
SELECT DISTINCT service_id
FROM prod_service_seed_rows
WHERE service_id IS NOT NULL;
-- Role: prod_bearers bearer-facing product rows staged before product inventory combine.

----------------------------------------------------------------------
-- BEARER_GE: Service → GE bearer mapping
----------------------------------------------------------------------
INSERT INTO prod_bearers SELECT 'BEARER_GE', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'BEARER_GE', BEARER_GE,
    'TRANSMISSION_INTID', TRANSMISSION_INTID
) FROM (
    SELECT SERVICE_ID, TRANSMISSION AS BEARER_GE, TRANSMISSION_INTID
    FROM prod_access_db.inca_src.V_T_INCATNT_SERVICE_TRANSMISSION_CURRENT
    WHERE SERVICE_ID IN (SELECT service_id FROM prod_services)
);
-- Role: prod_bearers bearer-facing product rows staged before product inventory combine.

----------------------------------------------------------------------
-- BEARER_G: GE → G walk (standard path for GE-bearer services)
----------------------------------------------------------------------
INSERT INTO prod_bearers SELECT 'BEARER_G', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'TRANSMISSION_INTID', TRANSMISSION_INTID,
    'BEARER_G', BEARER_G
) FROM (
    WITH bearer AS (
        SELECT row_data:SERVICE_ID::VARCHAR AS SERVICE_ID,
               row_data:BEARER_GE::VARCHAR AS BEARER_GE,
               row_data:TRANSMISSION_INTID::NUMBER AS TRANSMISSION_INTID
        FROM prod_bearers WHERE qid = 'BEARER_GE'
    )
    SELECT DISTINCT b.SERVICE_ID, b.TRANSMISSION_INTID,
           cp.PARENT_IDENTITY AS BEARER_G
    FROM prod_access_db.inca_src.V_T_INCATNT_CONTENT_POSITION_CURRENT cp
    JOIN bearer b ON cp.CHILD_IDENTITY = b.BEARER_GE
    WHERE cp.NIVKOD = 'S'
      AND cp.BFK_TRANSMISSION IS NOT NULL
);
-- Role: prod_bearers bearer-facing product rows staged before product inventory combine.

----------------------------------------------------------------------
-- BEARER_DIRECT: Direct bearer path (for SDH/STM services where
-- bearer IS the G-equivalent, trunk positions are direct children).
-- For GE services this produces harmless extra rows that find
-- nothing at NIVKOD='E' downstream.
----------------------------------------------------------------------
INSERT INTO prod_bearers SELECT 'BEARER_DIRECT', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'TRANSMISSION_INTID', TRANSMISSION_INTID,
    'BEARER_G', BEARER_G
) FROM (
    SELECT row_data:SERVICE_ID::VARCHAR AS SERVICE_ID,
           row_data:TRANSMISSION_INTID::NUMBER AS TRANSMISSION_INTID,
           row_data:BEARER_GE::VARCHAR AS BEARER_G
    FROM prod_bearers WHERE qid = 'BEARER_GE'
);
-- Role: prod_g_bearers G-bearer intermediate used to position bearer path rows.

CREATE OR REPLACE TEMP TABLE prod_g_bearers AS
SELECT DISTINCT
    row_data:SERVICE_ID::VARCHAR AS SERVICE_ID,
    row_data:BEARER_G::VARCHAR AS BEARER_G
FROM prod_bearers
WHERE qid IN ('BEARER_G', 'BEARER_DIRECT');
-- Role: prod_g_bearer_positions G-bearer intermediate used to position bearer path rows.

CREATE OR REPLACE TEMP TABLE prod_g_bearer_positions AS
SELECT
    gb.SERVICE_ID,
    gb.BEARER_G,
    cp.BFK_PCG,
    cp.PCGPOSITION,
    cp.CONTENT_STATUS_FROM,
    cp.CONTENT_DATE_FROM,
    cp.CONTENT_STATUS_UNTIL,
    cp.CONTENT_DATE_UNTIL,
    cp.BFK_TRANSMISSION,
    cp.NIVKOD
FROM prod_access_db.inca_src.V_T_INCATNT_CONTENT_POSITION_CURRENT cp
JOIN prod_g_bearers gb ON cp.CHILD_IDENTITY = gb.BEARER_G
WHERE (cp.NIVKOD = 'E' AND cp.BFK_PCG IS NOT NULL)
   OR (cp.BFK_TRANSMISSION IS NOT NULL AND cp.BFK_TRANSMISSION LIKE '%ODUC%');
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.

----------------------------------------------------------------------
-- TRUNK_ODF: positions along OL trunks, with Location Alias
----------------------------------------------------------------------
INSERT INTO prod_all SELECT 'TRUNK_ODF', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'SITE_CODE', SITE_CODE,
    'SITE_TYPE', SITE_TYPE,
    'SITE_TYPE_NO', SITE_TYPE_NO,
    'CABLING_LOCATION', CABLING_LOCATION,
    'CABLING_POINTS', CABLING_POINTS,
    'CONN_TYPE', CONN_TYPE,
    'LOCATION_ALIAS', LOCATION_ALIAS,
    'ROUTE_PATH', ROUTE_PATH,
    'POS', POS,
    'SITE_SIDE', SITE_SIDE,
    'PROT', PROT,
    'STATUS_O_TIME', STATUS_O_TIME,
    'O_TIME', O_TIME,
    'STATUS_T_TIME', STATUS_T_TIME,
    'T_TIME', T_TIME,
    'COMMENT', COMMENT,
    'FUNCTION', FUNCTION,
    'ROW_TYPE', ROW_TYPE,
    'FLOOR', FLOOR,
    'ROOM', ROOM,
    'ROW_', ROW_,
    'ROWSIDE', ROWSIDE,
    'RACK', RACK,
    'SHELF', SHELF,
    'SUBRACK', SUBRACK,
    'CONNECTION_POINT_NR', CONNECTION_POINT_NR
) FROM (
    WITH all_trunk_positions AS (
        SELECT cp.SERVICE_ID,
               cp.BFK_PCG AS TRUNK_NAME,
               cp.PCGPOSITION AS POS,
               cp.BFK_PCG || ' Pos ' || cp.PCGPOSITION::VARCHAR AS CCP_CONTENT,
               cp.CONTENT_STATUS_FROM AS CP_STATUS_FROM,
               cp.CONTENT_DATE_FROM AS CP_DATE_FROM,
               cp.CONTENT_STATUS_UNTIL AS CP_STATUS_UNTIL,
               cp.CONTENT_DATE_UNTIL AS CP_DATE_UNTIL
        FROM prod_g_bearer_positions cp
        WHERE cp.NIVKOD = 'E'
          AND cp.BFK_PCG IS NOT NULL
    )
    SELECT
        atp.SERVICE_ID,
        ccp.SITE_CODE,
        ccp.SITE_TYPE,
        ccp.SITE_TYPE_NO,
        ccp.LOCATION AS CABLING_LOCATION,
        ccp.CONNECTION_POINT_NR || ' ' || COALESCE(ccp.CONNECTION_POINT_SIDE, 'Cable') || '.' || ccp.CONNECTION_POINT_NR AS CABLING_POINTS,
        cacp.CONNECTOR_TYPE AS CONN_TYPE,
        ep.LOCATIONALIAS AS LOCATION_ALIAS,
        atp.TRUNK_NAME AS ROUTE_PATH,
        atp.POS,
        ccp.SITE_SIDE,
        ccp.PROTECTION AS PROT,
        atp.CP_STATUS_FROM AS STATUS_O_TIME,
        atp.CP_DATE_FROM AS O_TIME,
        atp.CP_STATUS_UNTIL AS STATUS_T_TIME,
        atp.CP_DATE_UNTIL AS T_TIME,
        ccp.COMMENTS AS COMMENT,
        ccp.FUNCTION,
        'TRUNK_ODF' AS ROW_TYPE,
        -- Phase 1: structured location fields from CCP
        ccp.FLOOR,
        ccp.ROOM,
        ccp.ROW_,
        ccp.ROWSIDE,
        ccp.RACK,
        ccp.SHELF,
        ccp.SUBRACK,
        ccp.CONNECTION_POINT_NR
    FROM prod_access_db.inca_src.V_T_INCATNT_CONTENT_CONNECTION_POINT_CURRENT ccp
    JOIN all_trunk_positions atp
        ON ccp.CONTENT = atp.CCP_CONTENT
    LEFT JOIN prod_access_db.inca_src.V_T_INCATNT_CONNECTION_CABLING_POINT_CURRENT cacp
        ON ccp.CONNPT_INT_ID = cacp.CONNPT_INT_ID
        AND COALESCE(cacp.CONNECTION_POINT_SIDE, 'Cable') = COALESCE(ccp.CONNECTION_POINT_SIDE, 'Cable')
        AND cacp.CABPT_INT_ID IS NOT NULL
    LEFT JOIN prod_access_db.inca_src.V_T_INCATNT_EQUIPMENT_PASSIVE_CURRENT ep
        ON ccp.SITE_CODE = ep.SITE_CODE
        AND ccp.SITE_TYPE = ep.SITE_TYPE
        AND NVL(ccp.SITE_TYPE_NO, '') = NVL(ep.SITE_TYPE_NO, '')
        AND ccp.RACK = ep.RACK
        AND ccp.SHELF = ep.SHELF
        AND NVL(ccp.SUBRACK, '') = NVL(ep.SUBRACK, '')
    WHERE (ccp.CONNECTION_POINT_SIDE IN ('Cable', 'Patch') OR ccp.CONNECTION_POINT_SIDE IS NULL)
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY ccp.CONNPT_INT_ID, ccp.CONNECTION_POINT_SIDE
        ORDER BY ep.LOCATIONALIAS DESC NULLS LAST
    ) = 1
);
-- Role: prod_device_ccp_base product device intermediate used to build port and device evidence rows.

----------------------------------------------------------------------
-- DEVICE: wave + router ports, BO ODF resolved via CABLING cable trace
----------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE prod_device_ccp_base AS
SELECT gb.SERVICE_ID,
       TRIM(COALESCE(nep.NEPART_SITE_CODE, ccp.SITE_CODE)) AS SITE_CODE,
       TRIM(COALESCE(nep.NEPART_SITE_TYPE, ccp.SITE_TYPE)) AS SITE_TYPE,
       ccp.SITE_TYPE_NO,
       ccp.NE, ccp.NE_PART, ccp.CONTENT AS DEVICE_CONTENT,
       ccp.CONTENT_INT_ID AS DEVICE_CONTENT_INT_ID,
       ccp.FUNCTION AS OPTIC_FUNCTION,
       ccp.LOCATION AS DEVICE_LOCATION,
       ccp.CONNECTION_POINT_NR, ccp.CONNPT_INT_ID,
       ccp.CONTENT_STATUS_FROM, ccp.CONTENT_FROM,
       ccp.CONTENT_STATUS_UNTIL, ccp.CONTENT_UNTIL,
       ccp.COMMENTS AS CCP_COMMENTS,
       ccp.PROTECTION,
       gb.BEARER_G AS ROUTE_PATH,
       -- Phase 1: device type from NE_PART, function from NE
       nep.NE_TYPE,
       ne.FUNCTION AS NE_FUNCTION,
       -- Phase 2A: structured port assembly fields
       ccp.SLOT,
       ccp.SUBSLOT,
       -- Device's own NE_PART physical location (fallback when BO ODF location empty)
       nep.FLOOR AS NEP_FLOOR,
       nep.ROOM AS NEP_ROOM,
       nep.ROW_ AS NEP_ROW,
       nep.RACK AS NEP_RACK,
       nep.SHELF AS NEP_SHELF,
       nep.LOCATION AS NEP_LOCATION
FROM prod_access_db.inca_src.V_T_INCATNT_CONTENT_CONNECTION_POINT_CURRENT ccp
JOIN prod_g_bearers gb ON ccp.CONTENT = gb.BEARER_G
LEFT JOIN prod_access_db.inca_src.V_T_INCATNT_NE_PART_CURRENT nep
    ON ccp.NE = nep.NE AND ccp.NE_PART = nep.NE_PART_NAME
LEFT JOIN prod_access_db.inca_src.V_T_INCATNT_NE_CURRENT ne
    ON ccp.NE = ne.NE AND ccp.SITE_CODE = ne.SITE_CODE
WHERE ccp.NE IS NOT NULL;
-- Role: prod_device_ccp product device intermediate used to build port and device evidence rows.

CREATE OR REPLACE TEMP TABLE prod_device_ccp AS
WITH slot_comments AS (
    SELECT
        cs.NE,
        cs.NE_PART_NAME,
        cs.SLOT,
        IFF(GROUPING(cs.SUBSLOT) = 1, NULL, cs.SUBSLOT) AS SUBSLOT,
        GROUPING(cs.SUBSLOT) AS SLOT_LEVEL,
        LISTAGG(DISTINCT NULLIF(TRIM(cs.COMMENTS), ''), '; ') WITHIN GROUP (ORDER BY NULLIF(TRIM(cs.COMMENTS), '')) AS SLOT_COMMENTS,
        LISTAGG(DISTINCT NULLIF(TRIM(cs.SUBCOMMENTS), ''), '; ') WITHIN GROUP (ORDER BY NULLIF(TRIM(cs.SUBCOMMENTS), '')) AS SLOT_SUBCOMMENTS
    FROM prod_access_db.inca_src.V_T_INCATNT_CONTENT_SLOT_CURRENT cs
    JOIN (
        SELECT DISTINCT NE, NE_PART, SLOT
        FROM prod_device_ccp_base
        WHERE NE IS NOT NULL AND NE_PART IS NOT NULL AND SLOT IS NOT NULL
    ) dk ON dk.NE = cs.NE AND dk.NE_PART = cs.NE_PART_NAME AND dk.SLOT = cs.SLOT
    WHERE NULLIF(TRIM(cs.COMMENTS), '') IS NOT NULL OR NULLIF(TRIM(cs.SUBCOMMENTS), '') IS NOT NULL
    GROUP BY GROUPING SETS ((cs.NE, cs.NE_PART_NAME, cs.SLOT, cs.SUBSLOT), (cs.NE, cs.NE_PART_NAME, cs.SLOT))
)
SELECT base.*,
       COALESCE(
           NULLIF(TRIM(base.CCP_COMMENTS), ''),
           NULLIF(sc_exact.SLOT_COMMENTS, ''),
           NULLIF(sc_exact.SLOT_SUBCOMMENTS, ''),
           NULLIF(sc_slot.SLOT_COMMENTS, ''),
           NULLIF(sc_slot.SLOT_SUBCOMMENTS, '')
       ) AS COMMENTS
FROM prod_device_ccp_base base
LEFT JOIN slot_comments sc_exact
    ON sc_exact.NE = base.NE
    AND sc_exact.NE_PART_NAME = base.NE_PART
    AND sc_exact.SLOT = base.SLOT
    AND sc_exact.SUBSLOT = base.SUBSLOT
    AND sc_exact.SLOT_LEVEL = 0
LEFT JOIN slot_comments sc_slot
    ON sc_slot.NE = base.NE
    AND sc_slot.NE_PART_NAME = base.NE_PART
    AND sc_slot.SLOT = base.SLOT
    AND sc_slot.SLOT_LEVEL = 1;
-- Role: prod_device_txrx product device intermediate used to build port and device evidence rows.

CREATE OR REPLACE TEMP TABLE prod_device_txrx AS
SELECT d.*,
       cacp.CABLING_POINT AS DIRECTION,
       cacp.CABPT_INT_ID AS DEVICE_CABPT_INT_ID,
       cacp.CONNECTOR_TYPE AS DEVICE_CONNECTOR_TYPE
FROM prod_device_ccp d
JOIN prod_access_db.inca_src.V_T_INCATNT_CONNECTION_CABLING_POINT_CURRENT cacp
    ON d.CONNPT_INT_ID = cacp.CONNPT_INT_ID
WHERE cacp.CABPT_INT_ID IS NOT NULL;
-- Role: prod_device_bo_odf_details product device intermediate used to build port and device evidence rows.

CREATE OR REPLACE TEMP TABLE prod_device_bo_odf_details AS
WITH cable_endpoint_map AS (
    SELECT cab.A_CABPT_INT_ID AS DEVICE_CABPT_INT_ID, cab.B_CABPT_INT_ID AS BO_ODF_CABPT_INT_ID
    FROM prod_access_db.inca_src.V_T_INCATNT_CABLING_CURRENT cab
    JOIN prod_device_txrx dt ON cab.A_CABPT_INT_ID = dt.DEVICE_CABPT_INT_ID
    WHERE cab.B_CABPT_INT_ID IS NOT NULL
    UNION ALL
    SELECT cab.B_CABPT_INT_ID AS DEVICE_CABPT_INT_ID, cab.A_CABPT_INT_ID AS BO_ODF_CABPT_INT_ID
    FROM prod_access_db.inca_src.V_T_INCATNT_CABLING_CURRENT cab
    JOIN prod_device_txrx dt ON cab.B_CABPT_INT_ID = dt.DEVICE_CABPT_INT_ID
    WHERE cab.A_CABPT_INT_ID IS NOT NULL AND cab.B_CABPT_INT_ID != cab.A_CABPT_INT_ID
)
SELECT dt.*,
    cem.BO_ODF_CABPT_INT_ID,
    cacp2.CABLING_POINT AS BO_ODF_PORT,
    cacp2.CONNECTOR_TYPE AS BO_ODF_CONNECTOR,
    cacp2.CONNPT_INT_ID AS BO_ODF_CONNPT_INT_ID,
    cacp2.LOCATION AS BO_CACP_LOCATION,
    cacp2.SITE_CODE AS BO_CACP_SITE_CODE,
    cacp2.SITE_TYPE AS BO_CACP_SITE_TYPE,
    cacp2.RACK AS BO_CACP_RACK,
    cacp2.SHELF AS BO_CACP_SHELF,
    cacp2.SUBRACK AS BO_CACP_SUBRACK
FROM prod_device_txrx dt
LEFT JOIN cable_endpoint_map cem
    ON cem.DEVICE_CABPT_INT_ID = dt.DEVICE_CABPT_INT_ID
LEFT JOIN prod_access_db.inca_src.V_T_INCATNT_CONNECTION_CABLING_POINT_CURRENT cacp2
    ON cem.BO_ODF_CABPT_INT_ID = cacp2.CABPT_INT_ID;
-- Role: prod_device_bo_odf_location product device intermediate used to build port and device evidence rows.

CREATE OR REPLACE TEMP TABLE prod_device_bo_odf_location AS
WITH bo_odf_ccp AS (
    SELECT ccp.*
    FROM prod_access_db.inca_src.V_T_INCATNT_CONTENT_CONNECTION_POINT_CURRENT ccp
    JOIN (
        SELECT DISTINCT BO_ODF_CONNPT_INT_ID AS CONNPT_INT_ID
        FROM prod_device_bo_odf_details
        WHERE BO_ODF_CONNPT_INT_ID IS NOT NULL
    ) bok ON ccp.CONNPT_INT_ID = bok.CONNPT_INT_ID
)
SELECT bo.*,
       COALESCE(ccp2.LOCATION, bo.BO_CACP_LOCATION) AS BO_ODF_LOCATION,
       COALESCE(ccp2.SITE_CODE, bo.BO_CACP_SITE_CODE) AS BO_SITE_CODE,
       COALESCE(ccp2.SITE_TYPE, bo.BO_CACP_SITE_TYPE) AS BO_SITE_TYPE,
       COALESCE(ccp2.RACK, bo.BO_CACP_RACK) AS BO_RACK,
       COALESCE(ccp2.SHELF, bo.BO_CACP_SHELF) AS BO_SHELF,
       COALESCE(ccp2.SUBRACK, bo.BO_CACP_SUBRACK) AS BO_SUBRACK,
       -- Phase 1: structured location fields from BO ODF CCP
       ccp2.FLOOR AS BO_FLOOR,
       ccp2.ROOM AS BO_ROOM,
       ccp2.ROW_ AS BO_ROW,
       ccp2.ROWSIDE AS BO_ROWSIDE
FROM prod_device_bo_odf_details bo
LEFT JOIN bo_odf_ccp ccp2
    ON bo.BO_ODF_CONNPT_INT_ID = ccp2.CONNPT_INT_ID;
-- Role: prod_device_bo_odf_alias product device intermediate used to build port and device evidence rows.

CREATE OR REPLACE TEMP TABLE prod_device_bo_odf_alias AS
SELECT bol.*,
       ep.LOCATIONALIAS AS LOCATION_ALIAS
FROM prod_device_bo_odf_location bol
LEFT JOIN prod_access_db.inca_src.V_T_INCATNT_EQUIPMENT_PASSIVE_CURRENT ep
    ON COALESCE(bol.BO_SITE_CODE, bol.SITE_CODE) = ep.SITE_CODE
    AND COALESCE(bol.BO_SITE_TYPE, bol.SITE_TYPE) = ep.SITE_TYPE
    AND NVL(bol.SITE_TYPE_NO, '') = NVL(ep.SITE_TYPE_NO, '')
    AND bol.BO_RACK = ep.RACK
    AND bol.BO_SHELF = ep.SHELF
    AND NVL(bol.BO_SUBRACK, '') = NVL(ep.SUBRACK, '')
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY bol.CONNPT_INT_ID, bol.DIRECTION
    ORDER BY ep.LOCATIONALIAS DESC NULLS LAST
) = 1;
-- Role: prod_device_rows product device intermediate used to build port and device evidence rows.

CREATE OR REPLACE TEMP TABLE prod_device_rows AS
SELECT
    SERVICE_ID,
    SITE_CODE,
    SITE_TYPE,
    SITE_TYPE_NO,
    NE,
    NE_PART,
    DEVICE_CONTENT,
    DEVICE_CONTENT_INT_ID,
    OPTIC_FUNCTION,
    DEVICE_LOCATION,
    NEP_LOCATION,
    CONNECTION_POINT_NR,
    DIRECTION,
    BO_ODF_LOCATION AS CABLING_LOCATION,
    BO_ODF_PORT || ' Cable.' || BO_ODF_PORT AS CABLING_POINTS,
    COALESCE(BO_ODF_CONNECTOR, DEVICE_CONNECTOR_TYPE) AS CONN_TYPE,
    LOCATION_ALIAS,
    ROUTE_PATH,
    '01' AS POS,
    PROTECTION AS PROT,
    CONTENT_STATUS_FROM AS STATUS_O_TIME,
    CONTENT_FROM AS O_TIME,
    CONTENT_STATUS_UNTIL AS STATUS_T_TIME,
    CONTENT_UNTIL AS T_TIME,
    COMMENTS AS COMMENT,
    'DEVICE' AS ROW_TYPE,
    -- Phase 1: structured fields from NE_PART + BO ODF CCP
    NE_TYPE,
    NE_FUNCTION,
    BO_FLOOR,
    BO_ROOM,
    BO_ROW,
    BO_ROWSIDE,
    BO_RACK,
    BO_SHELF,
    BO_SUBRACK AS BO_SUBRACK,
    -- Phase 2A: structured port assembly fields
    SLOT,
    SUBSLOT
FROM prod_device_bo_odf_alias;

-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.
INSERT INTO prod_all
SELECT 'DEVICE', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'SITE_CODE', SITE_CODE,
    'SITE_TYPE', SITE_TYPE,
    'SITE_TYPE_NO', SITE_TYPE_NO,
    'NE', NE,
    'NE_PART', NE_PART,
    'DEVICE_CONTENT', DEVICE_CONTENT,
    'DEVICE_CONTENT_INT_ID', DEVICE_CONTENT_INT_ID,
    'OPTIC_FUNCTION', OPTIC_FUNCTION,
    'DEVICE_LOCATION', DEVICE_LOCATION,
    'NEP_LOCATION', NEP_LOCATION,
    'CONNECTION_POINT_NR', CONNECTION_POINT_NR,
    'DIRECTION', DIRECTION,
    'CABLING_LOCATION', CABLING_LOCATION,
    'CABLING_POINTS', CABLING_POINTS,
    'CONN_TYPE', CONN_TYPE,
    'LOCATION_ALIAS', LOCATION_ALIAS,
    'ROUTE_PATH', ROUTE_PATH,
    'POS', POS,
    'PROT', PROT,
    'STATUS_O_TIME', STATUS_O_TIME,
    'O_TIME', O_TIME,
    'STATUS_T_TIME', STATUS_T_TIME,
    'T_TIME', T_TIME,
    'COMMENT', COMMENT,
    'ROW_TYPE', ROW_TYPE,
    'NE_TYPE', NE_TYPE,
    'NE_FUNCTION', NE_FUNCTION,
    'BO_FLOOR', BO_FLOOR,
    'BO_ROOM', BO_ROOM,
    'BO_ROW', BO_ROW,
    'BO_ROWSIDE', BO_ROWSIDE,
    'BO_RACK', BO_RACK,
    'BO_SHELF', BO_SHELF,
    'BO_SUBRACK', BO_SUBRACK,
    'SLOT', SLOT,
    'SUBSLOT', SUBSLOT
)
FROM prod_device_rows;
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.

----------------------------------------------------------------------
-- ODUC: chassis function for wave NE Information strings
----------------------------------------------------------------------
INSERT INTO prod_all SELECT 'ODUC', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'SITE_CODE', SITE_CODE,
    'NE', NE,
    'CHASSIS_FUNCTION', CHASSIS_FUNCTION,
    'ODUC_NAME', ODUC_NAME,
    'LOCATION', LOCATION,
    'CONNECTION_POINT_NR', CONNECTION_POINT_NR
) FROM (
    WITH oduc_trunks AS (
        SELECT DISTINCT cp.SERVICE_ID, cp.BFK_TRANSMISSION AS ODUC_NAME
        FROM prod_g_bearer_positions cp
        WHERE cp.BFK_TRANSMISSION IS NOT NULL
          AND cp.BFK_TRANSMISSION LIKE '%ODUC%'
    )
    SELECT
        ot.SERVICE_ID,
        ccp.SITE_CODE,
        ccp.NE,
        ccp.FUNCTION AS CHASSIS_FUNCTION,
        ot.ODUC_NAME,
        ccp.LOCATION,
        ccp.CONNECTION_POINT_NR
    FROM prod_access_db.inca_src.V_T_INCATNT_CONTENT_CONNECTION_POINT_CURRENT ccp
    JOIN oduc_trunks ot ON ccp.CONTENT = ot.ODUC_NAME
    WHERE ccp.NE IS NOT NULL
);
-- Role: prod_edge_roots product edge traversal intermediate used to build path evidence.

----------------------------------------------------------------------
-- EDGES: Hierarchy edge extraction for geographic ordering (L1-L5)
----------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE prod_edge_roots AS
SELECT
    pb.SERVICE_ID,
    pb.BEARER_GE,
    COALESCE(cp.BFK_TRANSMISSION, cp.BFK_PCG) AS EDGE_NAME,
    COALESCE(
        TRY_TO_NUMBER(cp.PCGPOSITION),
        TRY_TO_NUMBER(cp.TRANSMISSIONPOSITION),
        cp.CSPOSITION
    ) AS EDGE_POSITION,
    COALESCE(
        cp.FK_PCGPOSITION_INTID,
        cp.FK_TRANSPOSITION_INTID,
        cp.FK_CSPOSITION_INTID,
        cp.CHILD_INT_ID
    ) AS EDGE_POSITION_ID
FROM (
    SELECT row_data:SERVICE_ID::VARCHAR AS SERVICE_ID,
           row_data:BEARER_GE::VARCHAR AS BEARER_GE
    FROM prod_bearers WHERE qid = 'BEARER_GE'
) pb
JOIN prod_access_db.inca_src.V_T_INCATNT_CONTENT_POSITION_CURRENT cp
    ON cp.CHILD_IDENTITY = pb.BEARER_GE
WHERE COALESCE(cp.BFK_TRANSMISSION, cp.BFK_PCG) IS NOT NULL;
-- Role: prod_edge_walk product edge traversal intermediate used to build path evidence.

CREATE OR REPLACE TEMP TABLE prod_edge_walk AS
WITH RECURSIVE edge_walk(
    level_no,
    level_tag,
    service_id,
    parent_edge_name,
    edge_name,
    edge_position,
    edge_position_id,
    edge_position_path,
    path_text
) AS (
    SELECT
        1 AS level_no,
        'L1' AS level_tag,
        SERVICE_ID,
        BEARER_GE AS PARENT_EDGE_NAME,
        EDGE_NAME,
        EDGE_POSITION,
        EDGE_POSITION_ID,
        COALESCE(EDGE_POSITION, 0)::VARCHAR || ':' || COALESCE(EDGE_POSITION_ID, 0)::VARCHAR
            AS EDGE_POSITION_PATH,
        EDGE_NAME AS PATH_TEXT
    FROM prod_edge_roots

    UNION ALL

    SELECT
        ew.level_no + 1 AS level_no,
        CONCAT('L', (ew.level_no + 1)::VARCHAR) AS level_tag,
        ew.SERVICE_ID,
        ew.EDGE_NAME AS PARENT_EDGE_NAME,
        COALESCE(cp.BFK_TRANSMISSION, cp.BFK_PCG) AS EDGE_NAME,
        COALESCE(
            TRY_TO_NUMBER(cp.PCGPOSITION),
            TRY_TO_NUMBER(cp.TRANSMISSIONPOSITION),
            cp.CSPOSITION
        ) AS EDGE_POSITION,
        COALESCE(
            cp.FK_PCGPOSITION_INTID,
            cp.FK_TRANSPOSITION_INTID,
            cp.FK_CSPOSITION_INTID,
            cp.CHILD_INT_ID
        ) AS EDGE_POSITION_ID,
        ew.EDGE_POSITION_PATH || '>' ||
            COALESCE(
                TRY_TO_NUMBER(cp.PCGPOSITION),
                TRY_TO_NUMBER(cp.TRANSMISSIONPOSITION),
                cp.CSPOSITION,
                0
            )::VARCHAR || ':' ||
            COALESCE(
                cp.FK_PCGPOSITION_INTID,
                cp.FK_TRANSPOSITION_INTID,
                cp.FK_CSPOSITION_INTID,
                cp.CHILD_INT_ID,
                0
            )::VARCHAR AS EDGE_POSITION_PATH,
        ew.PATH_TEXT || ' > ' || COALESCE(cp.BFK_TRANSMISSION, cp.BFK_PCG) AS PATH_TEXT
    FROM edge_walk ew
    JOIN prod_access_db.inca_src.V_T_INCATNT_CONTENT_POSITION_CURRENT cp
        ON cp.CHILD_IDENTITY = ew.EDGE_NAME
    WHERE ew.level_no < 8
      AND COALESCE(cp.BFK_TRANSMISSION, cp.BFK_PCG) IS NOT NULL
)
SELECT
    level_no,
    level_tag,
    service_id,
    parent_edge_name,
    edge_name,
    edge_position,
    edge_position_id,
    edge_position_path,
    path_text
FROM edge_walk;

INSERT INTO prod_edges
SELECT level_tag, service_id, edge_name
FROM prod_edge_walk;
-- Role: prod_edge_rows product edge traversal intermediate used to build path evidence.

-- Emit distinct edges into prod_all
CREATE OR REPLACE TEMP TABLE prod_edge_rows AS
SELECT DISTINCT SERVICE_ID, LEVEL_TAG, EDGE_NAME
FROM prod_edges;

-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.
INSERT INTO prod_all
SELECT 'EDGES', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'LEVEL', LEVEL_TAG,
    'EDGE_NAME', EDGE_NAME
)
FROM prod_edge_rows;
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.

----------------------------------------------------------------------
-- DP_SDP: demarcation point ODF rows from dedicated DP view.
-- Demarcation points are NOT in CCP — they have their own view
-- (prod_access_db.inca_src.V_T_INCATNT_DEMARCATION_POINT_CURRENT) keyed by SERVICE_ID
-- in the CONTENT column.
----------------------------------------------------------------------
-- Role: prod_dp_demarcation_points normalized demarcation point source rows keyed by service ID.
CREATE OR REPLACE TEMP TABLE prod_dp_demarcation_points AS
SELECT
    dp.*,
    SPLIT_PART(dp.CONTENT, ' ', 1) AS SERVICE_ID_KEY
FROM prod_access_db.inca_src.V_T_INCATNT_DEMARCATION_POINT_CURRENT dp;
-- Role: prod_dp_sdp_rows DP/SDP demarcation rows staged before product inventory combine.

CREATE OR REPLACE TEMP TABLE prod_dp_sdp_rows AS
SELECT
    dp.SERVICE_ID_KEY AS SERVICE_ID,
    dp.SITE_CODE,
    dp.SITE_TYPE,
    dp.SITE_TYPE_NO,
    CASE WHEN dp.SDP = 'X' THEN 'SDP ODF' ELSE 'DP ODF' END AS NE_INFORMATION,
    dp.LOCATION AS CABLING_LOCATION,
    dp.CONNECTION_POINT AS CABLING_POINTS,
    COALESCE(dp.CONNECTOR_TYPE, dp_cacp.CONNECTOR_TYPE) AS CONN_TYPE,
    ep.LOCATIONALIAS AS LOCATION_ALIAS,
    dp.POS,
    'Demarcation point: ' || dp.SITE_CODE || ' ' || dp.SITE_TYPE || COALESCE(' ' || dp.SITE_TYPE_NO::VARCHAR, '')
        || COALESCE(' ' || dp.NWP_ID::VARCHAR, '') || COALESCE(' ' || dp.NWP_CUSTOMER, '') AS ROUTE_PATH,
    NULL AS PROT,
    dp.CONTENT_STATUS_FROM AS STATUS_O_TIME,
    dp.CONTENT_DATE_FROM AS O_TIME,
    dp.CONTENT_STATUS_UNTIL AS STATUS_T_TIME,
    dp.CONTENT_DATE_UNTIL AS T_TIME,
    dp.COMMENTS AS COMMENT,
    dp.FUNCTION,
    CASE WHEN dp.SDP = 'X' THEN 'SDP_ODF' ELSE 'DP_ODF' END AS ROW_TYPE,
    dp.NWP_CUSTOMER,
    dp.NWP_ID,
    dp.CONN_POINT_INT_ID,
    CASE WHEN dp.RACK IS NOT NULL THEN 'ARELION' ELSE 'EXTERNAL' END AS DP_OWNER
FROM prod_dp_demarcation_points dp
JOIN prod_services s ON dp.SERVICE_ID_KEY = s.SERVICE_ID
LEFT JOIN prod_access_db.inca_src.V_T_INCATNT_CONNECTION_CABLING_POINT_CURRENT dp_cacp
    ON dp.CONN_POINT_INT_ID = dp_cacp.CONNPT_INT_ID
    AND (dp_cacp.CONNECTION_POINT_SIDE = 'Cable' OR dp_cacp.CONNECTION_POINT_SIDE IS NULL)
LEFT JOIN prod_access_db.inca_src.V_T_INCATNT_EQUIPMENT_PASSIVE_CURRENT ep
    ON dp.SITE_CODE = ep.SITE_CODE AND dp.SITE_TYPE = ep.SITE_TYPE
    AND NVL(dp.SITE_TYPE_NO, '') = NVL(ep.SITE_TYPE_NO, '')
    AND dp.RACK = ep.RACK AND dp.SHELF = ep.SHELF
    AND NVL(dp.SUBRACK, '') = NVL(ep.SUBRACK, '')
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY COALESCE(dp.CONN_POINT_INT_ID::VARCHAR, dp.CONTENT || '|' || dp.SITE_CODE || '|' || dp.POS::VARCHAR)
    ORDER BY IFF(COALESCE(dp.CONNECTOR_TYPE, dp_cacp.CONNECTOR_TYPE) IS NOT NULL, 1, 0) DESC,
             IFF(dp_cacp.CONNECTION_POINT_SIDE = 'Cable', 1, 0) DESC, ep.LOCATIONALIAS DESC NULLS LAST
) = 1;
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.

INSERT INTO prod_all SELECT 'DP_SDP', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'SITE_CODE', SITE_CODE,
    'SITE_TYPE', SITE_TYPE,
    'SITE_TYPE_NO', SITE_TYPE_NO,
    'NE_INFORMATION', NE_INFORMATION,
    'CABLING_LOCATION', CABLING_LOCATION,
    'CABLING_POINTS', CABLING_POINTS,
    'CONN_TYPE', CONN_TYPE,
    'LOCATION_ALIAS', LOCATION_ALIAS,
    'POS', POS,
    'ROUTE_PATH', ROUTE_PATH,
    'PROT', PROT,
    'STATUS_O_TIME', STATUS_O_TIME,
    'O_TIME', O_TIME,
    'STATUS_T_TIME', STATUS_T_TIME,
    'T_TIME', T_TIME,
    'COMMENT', COMMENT,
    'FUNCTION', FUNCTION,
    'ROW_TYPE', ROW_TYPE,
    'NWP_CUSTOMER', NWP_CUSTOMER,
    'NWP_ID', NWP_ID,
    'CONN_POINT_INT_ID', CONN_POINT_INT_ID,
    'DP_OWNER', DP_OWNER
) FROM prod_dp_sdp_rows;
-- Role: prod_metadata_edge_names product metadata intermediate used to enrich final product rows.

----------------------------------------------------------------------
-- TRUNK_METADATA: PCG metadata for trunk endpoint/type resolution.
-- Provides A_SITE_CODE, B_SITE_CODE, MEDIA, PREFIX (trunk type)
-- for trunks referenced in this export's EDGES.
----------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE prod_metadata_edge_names AS
SELECT DISTINCT EDGE_NAME
FROM prod_edges
WHERE EDGE_NAME IS NOT NULL;
-- Role: prod_trunk_metadata_rows product metadata intermediate used to enrich final product rows.

CREATE OR REPLACE TEMP TABLE prod_trunk_metadata_rows AS
SELECT DISTINCT
    p.BPK_PCG,
    p.A_SITE_CODE,
    p.A_SITE_TYPE,
    p.A_SITE_TYPE_NUMBER,
    p.B_SITE_CODE,
    p.B_SITE_TYPE,
    p.B_SITE_TYPE_NUMBER,
    p.MEDIA,
    p.PREFIX
FROM prod_access_db.inca_src.V_T_INCATNT_PCG_CURRENT p
JOIN prod_metadata_edge_names edge_names
    ON p.BPK_PCG = edge_names.EDGE_NAME;
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.

INSERT INTO prod_all SELECT 'TRUNK_METADATA', OBJECT_CONSTRUCT(
    'BPK_PCG', BPK_PCG,
    'A_SITE_CODE', A_SITE_CODE,
    'A_SITE_TYPE', A_SITE_TYPE,
    'A_SITE_TYPE_NUMBER', A_SITE_TYPE_NUMBER,
    'B_SITE_CODE', B_SITE_CODE,
    'B_SITE_TYPE', B_SITE_TYPE,
    'B_SITE_TYPE_NUMBER', B_SITE_TYPE_NUMBER,
    'MEDIA', MEDIA,
    'PREFIX', PREFIX
)
FROM prod_trunk_metadata_rows;
-- Role: prod_transmission_metadata_rows product metadata intermediate used to enrich final product rows.

----------------------------------------------------------------------
-- TRANSMISSION_METADATA: Transmission metadata for transport-level
-- edge endpoint/type resolution.  Provides A_SITE_CODE, B_SITE_CODE
-- for WDM, MCH, ODUC, OCGX and bearer-level edges referenced in
-- this export's EDGES that are NOT in PCG (trunk) metadata.
----------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE prod_transmission_metadata_rows AS
SELECT DISTINCT
    t.BPK_TRANSMISSION,
    t.OBJECTTYPE,
    t.A_SITE_CODE,
    t.A_SITE_TYPE,
    t.A_SITE_TYPE_NUMBER,
    t.B_SITE_CODE,
    t.B_SITE_TYPE,
    t.B_SITE_TYPE_NUMBER,
    t.PREFIX
FROM prod_access_db.inca_src.V_T_INCATNT_TRANSMISSION_CURRENT t
JOIN prod_metadata_edge_names edge_names
    ON t.BPK_TRANSMISSION = edge_names.EDGE_NAME;
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.

INSERT INTO prod_all SELECT 'TRANSMISSION_METADATA', OBJECT_CONSTRUCT(
    'BPK_TRANSMISSION', BPK_TRANSMISSION,
    'OBJECTTYPE', OBJECTTYPE,
    'A_SITE_CODE', A_SITE_CODE,
    'A_SITE_TYPE', A_SITE_TYPE,
    'A_SITE_TYPE_NUMBER', A_SITE_TYPE_NUMBER,
    'B_SITE_CODE', B_SITE_CODE,
    'B_SITE_TYPE', B_SITE_TYPE,
    'B_SITE_TYPE_NUMBER', B_SITE_TYPE_NUMBER,
    'PREFIX', PREFIX
)
FROM prod_transmission_metadata_rows;

-- Role: prod_transport_device_endpoint_rows product route-order proof intermediate.

----------------------------------------------------------------------
-- TRANSPORT_DEVICE_ADJACENCY: device-to-device transport proof.
-- Uses recursive content-position edges plus CCP device endpoint rows.
-- Emits an adjacency only when Snowflake provides exactly two distinct
-- device endpoint sites for the same transport edge. This includes lower
-- child edges (for example OTUC/WDM under an ODU) when parent transport
-- edges do not carry endpoint CCP rows directly.
----------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE prod_transport_device_endpoint_rows AS
SELECT
    walk.service_id,
    walk.edge_name,
    walk.level_no,
    walk.level_tag,
    walk.parent_edge_name,
    walk.edge_position,
    walk.edge_position_id,
    walk.edge_position_path,
    walk.path_text,
    ccp.CONNPT_INT_ID,
    COALESCE(NULLIF(TRIM(nep.NEPART_SITE_CODE), ''), ccp.SITE_CODE) AS DEVICE_SITE_CODE,
    COALESCE(NULLIF(TRIM(nep.NEPART_SITE_TYPE), ''), ccp.SITE_TYPE) AS DEVICE_SITE_TYPE,
    ccp.NE,
    ccp.NE_PART,
    ccp.FUNCTION,
    ccp.CONNECTION_POINT_NR,
    ccp.SLOT,
    ccp.SUBSLOT,
    ccp.LOCATION
FROM prod_edge_walk walk
JOIN prod_transmission_metadata_rows tx
    ON tx.BPK_TRANSMISSION = walk.edge_name
JOIN prod_access_db.inca_src.V_T_INCATNT_CONTENT_CONNECTION_POINT_CURRENT ccp
    ON ccp.CONTENT = walk.edge_name
    AND ccp.NE IS NOT NULL
LEFT JOIN prod_access_db.inca_src.V_T_INCATNT_NE_PART_CURRENT nep
    ON ccp.NE = nep.NE
    AND ccp.NE_PART = nep.NE_PART_NAME
WHERE COALESCE(NULLIF(TRIM(nep.NEPART_SITE_CODE), ''), ccp.SITE_CODE) IS NOT NULL;
-- Role: prod_transport_device_adjacency_rows product route-order proof rows.

CREATE OR REPLACE TEMP TABLE prod_transport_device_adjacency_rows AS
WITH endpoint_sites AS (
    SELECT DISTINCT
        service_id,
        edge_name,
        level_no,
        level_tag,
        parent_edge_name,
        edge_position,
        edge_position_id,
        edge_position_path,
        path_text,
        connpt_int_id,
        device_site_code,
        device_site_type,
        ne,
        ne_part,
        function,
        connection_point_nr,
        slot,
        subslot,
        location
    FROM prod_transport_device_endpoint_rows
),
device_row_keys AS (
    SELECT DISTINCT
        service_id,
        site_code AS device_site_code,
        site_type AS device_site_type,
        ne,
        ne_part,
        optic_function,
        device_location,
        device_content,
        device_content_int_id,
        ne_type,
        ne_function,
        route_path AS device_route_path,
        slot AS device_slot,
        subslot AS device_subslot,
        connection_point_nr AS device_connection_point_nr,
        CASE
            WHEN UPPER(COALESCE(ne_type, '')) IN ('WS', 'MOTR', 'OTN')
              OR UPPER(COALESCE(ne_part, '')) LIKE '%WS%'
                THEN 'CIENA_WS_MOTR_OTN'
            WHEN UPPER(COALESCE(ne_type, '')) IN ('G30', 'G31', 'G40')
              OR UPPER(COALESCE(ne_part, '')) LIKE 'G3%'
              OR UPPER(COALESCE(ne_part, '')) LIKE 'G4%'
                THEN 'G30_G40'
            WHEN UPPER(COALESCE(ne_type, '')) = 'DTN'
              OR UPPER(COALESCE(ne_part, '')) LIKE 'XTC%'
                THEN 'DTN'
            WHEN UPPER(COALESCE(ne_type, '')) IN ('TM', 'OTM32D', 'OTM40D', 'OTM96D', 'OADM')
              OR UPPER(COALESCE(ne_type, '')) LIKE 'OTM%'
                THEN 'OTM_TM'
            ELSE COALESCE(NULLIF(ne_type, ''), 'UNKNOWN')
        END AS device_platform_family
    FROM prod_device_rows
    WHERE ne IS NOT NULL
      AND ne_part IS NOT NULL
      AND slot IS NOT NULL
      AND subslot IS NOT NULL
),
device_endpoint_candidates AS (
    SELECT DISTINCT
        endpoint_sites.*,
        device_row_keys.ne_type AS device_ne_type,
        device_row_keys.ne_function AS device_ne_function,
        device_row_keys.device_route_path,
        device_row_keys.device_slot,
        device_row_keys.device_subslot,
        device_row_keys.device_connection_point_nr,
        device_row_keys.device_platform_family AS platform_family,
        CASE
            WHEN device_row_keys.device_subslot = endpoint_sites.connection_point_nr
                THEN 'DEVICE_SUBSLOT_EQUALS_CCP_CONNECTION_POINT_NR'
            WHEN device_row_keys.device_platform_family = 'G30_G40'
                AND REGEXP_LIKE(UPPER(device_row_keys.device_subslot), '^T[0-9]+$')
                AND LTRIM(REGEXP_REPLACE(UPPER(device_row_keys.device_subslot), '^T', ''), '0')
                    = LTRIM(endpoint_sites.connection_point_nr, '0')
                THEN 'T_PORT_TO_CONNECTION_POINT_NR'
            ELSE NULL
        END AS port_match_rule,
        'prod_transport_device_endpoint_rows + prod_device_rows' AS port_match_source_view,
        OBJECT_CONSTRUCT(
            'edge_position_id', endpoint_sites.edge_position_id,
            'edge_position_path', endpoint_sites.edge_position_path,
            'path_text', endpoint_sites.path_text,
            'endpoint_location', endpoint_sites.location,
            'device_route_path', device_row_keys.device_route_path,
            'device_content', device_row_keys.device_content,
            'device_content_int_id', device_row_keys.device_content_int_id,
            'metadata_a_site_code', tx.A_SITE_CODE,
            'metadata_b_site_code', tx.B_SITE_CODE
        )::VARCHAR AS port_match_source_ids
    FROM endpoint_sites
    JOIN prod_transmission_metadata_rows tx
        ON tx.BPK_TRANSMISSION = endpoint_sites.edge_name
        AND endpoint_sites.device_site_code IN (tx.A_SITE_CODE, tx.B_SITE_CODE)
    JOIN device_row_keys
        ON device_row_keys.service_id = endpoint_sites.service_id
        AND device_row_keys.device_site_code = endpoint_sites.device_site_code
        AND device_row_keys.ne = endpoint_sites.ne
        AND device_row_keys.ne_part = endpoint_sites.ne_part
        AND device_row_keys.device_slot = endpoint_sites.slot
    WHERE endpoint_sites.slot IS NOT NULL
      AND endpoint_sites.connection_point_nr IS NOT NULL
      AND device_row_keys.device_platform_family <> 'DTN'
),
content_position_endpoint_candidates AS (
    SELECT DISTINCT
        endpoint_sites.*,
        device_row_keys.ne_type AS device_ne_type,
        device_row_keys.ne_function AS device_ne_function,
        device_row_keys.device_route_path,
        device_row_keys.device_slot,
        device_row_keys.device_subslot,
        device_row_keys.device_connection_point_nr,
        device_row_keys.device_platform_family AS platform_family,
        'CONTENT_POSITION_TO_LINE_ENDPOINT' AS port_match_rule,
        'prod_transport_device_endpoint_rows + prod_device_rows'
            AS port_match_source_view,
        OBJECT_CONSTRUCT(
            'edge_position_id', endpoint_sites.edge_position_id,
            'edge_position_path', endpoint_sites.edge_position_path,
            'path_text', endpoint_sites.path_text,
            'endpoint_location', endpoint_sites.location,
            'device_route_path', device_row_keys.device_route_path,
            'device_content', device_row_keys.device_content,
            'device_content_int_id', device_row_keys.device_content_int_id
        )::VARCHAR AS port_match_source_ids
    FROM endpoint_sites
    JOIN device_row_keys
        ON device_row_keys.service_id = endpoint_sites.service_id
        AND device_row_keys.device_site_code = endpoint_sites.device_site_code
        AND device_row_keys.ne = endpoint_sites.ne
        AND device_row_keys.ne_part = endpoint_sites.ne_part
        AND device_row_keys.device_slot = endpoint_sites.slot
        AND device_row_keys.device_platform_family = 'OTM_TM'
    WHERE endpoint_sites.slot IS NOT NULL
      AND endpoint_sites.subslot IS NOT NULL
      AND device_row_keys.device_subslot = endpoint_sites.subslot
      AND endpoint_sites.level_no > 1
    UNION ALL
    SELECT DISTINCT
        walk.service_id,
        walk.edge_name,
        walk.level_no,
        walk.level_tag,
        walk.parent_edge_name,
        walk.edge_position,
        walk.edge_position_id,
        walk.edge_position_path,
        walk.path_text,
        NULL AS connpt_int_id,
        device_row_keys.device_site_code,
        device_row_keys.device_site_type,
        device_row_keys.ne,
        device_row_keys.ne_part,
        device_row_keys.optic_function AS function,
        device_row_keys.device_connection_point_nr AS connection_point_nr,
        device_row_keys.device_slot AS slot,
        device_row_keys.device_subslot AS subslot,
        device_row_keys.device_location AS location,
        device_row_keys.ne_type AS device_ne_type,
        device_row_keys.ne_function AS device_ne_function,
        device_row_keys.device_route_path,
        device_row_keys.device_slot,
        device_row_keys.device_subslot,
        device_row_keys.device_connection_point_nr,
        device_row_keys.device_platform_family AS platform_family,
        'CONTENT_POSITION_TO_LINE_ENDPOINT' AS port_match_rule,
        'V_T_INCATNT_CONTENT_POSITION_CURRENT + prod_edge_walk + prod_device_rows'
            AS port_match_source_view,
        OBJECT_CONSTRUCT(
            'edge_position_id', walk.edge_position_id,
            'edge_position_path', walk.edge_position_path,
            'path_text', walk.path_text,
            'device_location', device_row_keys.device_location,
            'device_route_path', device_row_keys.device_route_path,
            'device_content', device_row_keys.device_content,
            'device_content_int_id', device_row_keys.device_content_int_id,
            'child_parent_child_int_id', child_parent.CHILD_INT_ID,
            'child_parent_transmission_intid', child_parent.TRANSMISSION_INTID,
            'edge_parent_child_int_id', edge_parent.CHILD_INT_ID,
            'edge_parent_bfk_transmission', edge_parent.BFK_TRANSMISSION,
            'metadata_a_site_code', tx.A_SITE_CODE,
            'metadata_b_site_code', tx.B_SITE_CODE
        )::VARCHAR AS port_match_source_ids
    FROM prod_edge_walk walk
    JOIN prod_transmission_metadata_rows tx
        ON tx.BPK_TRANSMISSION = walk.edge_name
    JOIN device_row_keys
        ON device_row_keys.service_id = walk.service_id
        AND device_row_keys.device_platform_family = 'OTM_TM'
    JOIN prod_access_db.inca_src.V_T_INCATNT_CONTENT_POSITION_CURRENT child_parent
        ON child_parent.CHILD_INT_ID = device_row_keys.device_content_int_id
    JOIN prod_access_db.inca_src.V_T_INCATNT_CONTENT_POSITION_CURRENT edge_parent
        ON edge_parent.CHILD_INT_ID = child_parent.TRANSMISSION_INTID
        AND edge_parent.BFK_TRANSMISSION = walk.edge_name
    WHERE device_row_keys.device_content_int_id IS NOT NULL
      AND device_row_keys.device_slot IS NOT NULL
      AND device_row_keys.device_subslot IS NOT NULL
      AND walk.level_no > 1
      AND device_row_keys.device_site_code IN (tx.A_SITE_CODE, tx.B_SITE_CODE)
),
dwdm_cabling_endpoint_candidates AS (
    SELECT DISTINCT
        endpoint_sites.*,
        device_row_keys.ne_type AS device_ne_type,
        device_row_keys.ne_function AS device_ne_function,
        device_row_keys.device_route_path,
        device_row_keys.device_slot,
        device_row_keys.device_subslot,
        device_row_keys.device_connection_point_nr,
        device_row_keys.device_platform_family AS platform_family,
        'CABLING_POINT_TO_PEER_CABLING_POINT' AS port_match_rule,
        'prod_transport_device_endpoint_rows + prod_device_rows + V_T_INCATNT_CONNECTION_CABLING_POINT_CURRENT + V_T_INCATNT_CABLING_CURRENT'
            AS port_match_source_view,
        OBJECT_CONSTRUCT(
            'edge_position_id', endpoint_sites.edge_position_id,
            'edge_position_path', endpoint_sites.edge_position_path,
            'path_text', endpoint_sites.path_text,
            'endpoint_connpt_int_id', endpoint_sites.connpt_int_id,
            'endpoint_cabpt_int_id', endpoint_cacp.CABPT_INT_ID,
            'peer_cabpt_int_id', peer_cacp.CABPT_INT_ID,
            'device_route_path', device_row_keys.device_route_path,
            'device_content', device_row_keys.device_content,
            'device_content_int_id', device_row_keys.device_content_int_id
        )::VARCHAR AS port_match_source_ids
    FROM endpoint_sites
    JOIN device_row_keys
        ON device_row_keys.service_id = endpoint_sites.service_id
        AND device_row_keys.device_site_code = endpoint_sites.device_site_code
        AND device_row_keys.ne = endpoint_sites.ne
        AND device_row_keys.ne_part = endpoint_sites.ne_part
        AND device_row_keys.device_slot = endpoint_sites.slot
        AND device_row_keys.device_platform_family = 'DTN'
    JOIN prod_access_db.inca_src.V_T_INCATNT_CONNECTION_CABLING_POINT_CURRENT endpoint_cacp
        ON TO_VARCHAR(endpoint_cacp.CONNPT_INT_ID) = TO_VARCHAR(endpoint_sites.connpt_int_id)
        AND endpoint_cacp.CABPT_INT_ID IS NOT NULL
    JOIN prod_access_db.inca_src.V_T_INCATNT_CABLING_CURRENT cab
        ON cab.A_CABPT_INT_ID = endpoint_cacp.CABPT_INT_ID
        OR cab.B_CABPT_INT_ID = endpoint_cacp.CABPT_INT_ID
    JOIN prod_access_db.inca_src.V_T_INCATNT_CONNECTION_CABLING_POINT_CURRENT peer_cacp
        ON peer_cacp.CABPT_INT_ID IS NOT NULL
        AND peer_cacp.CABPT_INT_ID != endpoint_cacp.CABPT_INT_ID
        AND (
            (
                cab.A_CABPT_INT_ID = endpoint_cacp.CABPT_INT_ID
                AND peer_cacp.CABPT_INT_ID = cab.B_CABPT_INT_ID
            )
            OR (
                cab.B_CABPT_INT_ID = endpoint_cacp.CABPT_INT_ID
                AND peer_cacp.CABPT_INT_ID = cab.A_CABPT_INT_ID
            )
        )
    WHERE endpoint_sites.connpt_int_id IS NOT NULL
      AND endpoint_sites.slot IS NOT NULL
),
candidate_endpoint_sites AS (
    SELECT device_endpoint_candidates.*, 'EXACT_DEVICE_PORT_MATCH' AS endpoint_proof_source, 1 AS proof_priority
    FROM device_endpoint_candidates
    WHERE port_match_rule IS NOT NULL
    UNION ALL
    SELECT dwdm_cabling_endpoint_candidates.*, 'EXACT_DEVICE_PORT_MATCH' AS endpoint_proof_source, 1 AS proof_priority
    FROM dwdm_cabling_endpoint_candidates
    UNION ALL
    SELECT content_position_endpoint_candidates.*, 'EXACT_DEVICE_PORT_MATCH' AS endpoint_proof_source, 0 AS proof_priority
    FROM content_position_endpoint_candidates
),
candidate_site_counts AS (
    SELECT
        service_id,
        edge_name,
        port_match_rule,
        endpoint_proof_source,
        COUNT(*) AS endpoint_row_count,
        COUNT(DISTINCT device_site_code) AS endpoint_site_count,
        COUNT(*) - COUNT(DISTINCT
            device_site_code || '|' || ne || '|' || ne_part || '|' ||
            device_slot || '|' || device_subslot || '|' ||
            slot || '|' || connection_point_nr
        ) AS duplicate_endpoint_count,
        SUM(IFF(
            device_site_code IS NULL
            OR ne IS NULL
            OR ne_part IS NULL
            OR device_slot IS NULL
            OR device_subslot IS NULL
            OR slot IS NULL
            OR connection_point_nr IS NULL,
            1,
            0
        )) AS null_endpoint_count
    FROM candidate_endpoint_sites
    GROUP BY service_id, edge_name, port_match_rule, endpoint_proof_source
),
ranked_endpoints AS (
    SELECT candidate_endpoint_sites.*,
           candidate_site_counts.endpoint_row_count,
           candidate_site_counts.duplicate_endpoint_count,
           candidate_site_counts.null_endpoint_count,
           ROW_NUMBER() OVER (
                PARTITION BY
                    candidate_endpoint_sites.service_id,
                    candidate_endpoint_sites.edge_name,
                    candidate_endpoint_sites.port_match_rule,
                    candidate_endpoint_sites.endpoint_proof_source
                ORDER BY
                    candidate_endpoint_sites.device_site_code,
                    candidate_endpoint_sites.ne,
                    candidate_endpoint_sites.ne_part,
                    candidate_endpoint_sites.device_slot,
                    candidate_endpoint_sites.device_subslot,
                    candidate_endpoint_sites.connection_point_nr
            ) AS endpoint_rank
    FROM candidate_endpoint_sites
    JOIN candidate_site_counts
        ON candidate_site_counts.service_id = candidate_endpoint_sites.service_id
        AND candidate_site_counts.edge_name = candidate_endpoint_sites.edge_name
        AND candidate_site_counts.port_match_rule = candidate_endpoint_sites.port_match_rule
        AND candidate_site_counts.endpoint_proof_source = candidate_endpoint_sites.endpoint_proof_source
        AND candidate_site_counts.endpoint_site_count = 2
        AND candidate_site_counts.endpoint_row_count = 2
        AND candidate_site_counts.duplicate_endpoint_count = 0
        AND candidate_site_counts.null_endpoint_count = 0
),
candidate_pairs AS (
SELECT
    first_endpoint.service_id,
    first_endpoint.edge_name,
    first_endpoint.level_no,
    first_endpoint.level_tag,
    first_endpoint.parent_edge_name,
    first_endpoint.edge_position,
    first_endpoint.edge_position_id,
    first_endpoint.edge_position_path,
    first_endpoint.path_text,
    first_endpoint.port_match_rule,
    first_endpoint.port_match_source_view,
    first_endpoint.port_match_source_ids,
    IFF(
        first_endpoint.platform_family = second_endpoint.platform_family,
        first_endpoint.platform_family,
        'MIXED_PLATFORM'
    ) AS PLATFORM_FAMILY,
    first_endpoint.device_site_code AS ENDPOINT_1_SITE_CODE,
    first_endpoint.device_site_type AS ENDPOINT_1_SITE_TYPE,
    first_endpoint.ne AS ENDPOINT_1_NE,
    first_endpoint.ne_part AS ENDPOINT_1_NE_PART,
    first_endpoint.function AS ENDPOINT_1_FUNCTION,
    first_endpoint.device_connection_point_nr AS ENDPOINT_1_DEVICE_CONNECTION_POINT_NR,
    first_endpoint.device_slot AS ENDPOINT_1_DEVICE_SLOT,
    first_endpoint.device_subslot AS ENDPOINT_1_DEVICE_SUBSLOT,
    first_endpoint.connection_point_nr AS ENDPOINT_1_CCP_CONNECTION_POINT_NR,
    first_endpoint.slot AS ENDPOINT_1_CCP_SLOT,
    first_endpoint.subslot AS ENDPOINT_1_CCP_SUBSLOT,
    first_endpoint.connection_point_nr AS ENDPOINT_1_CONNECTION_POINT_NR,
    first_endpoint.slot AS ENDPOINT_1_SLOT,
    first_endpoint.subslot AS ENDPOINT_1_SUBSLOT,
    first_endpoint.location AS ENDPOINT_1_LOCATION,
    second_endpoint.device_site_code AS ENDPOINT_2_SITE_CODE,
    second_endpoint.device_site_type AS ENDPOINT_2_SITE_TYPE,
    second_endpoint.ne AS ENDPOINT_2_NE,
    second_endpoint.ne_part AS ENDPOINT_2_NE_PART,
    second_endpoint.function AS ENDPOINT_2_FUNCTION,
    second_endpoint.device_connection_point_nr AS ENDPOINT_2_DEVICE_CONNECTION_POINT_NR,
    second_endpoint.device_slot AS ENDPOINT_2_DEVICE_SLOT,
    second_endpoint.device_subslot AS ENDPOINT_2_DEVICE_SUBSLOT,
    second_endpoint.connection_point_nr AS ENDPOINT_2_CCP_CONNECTION_POINT_NR,
    second_endpoint.slot AS ENDPOINT_2_CCP_SLOT,
    second_endpoint.subslot AS ENDPOINT_2_CCP_SUBSLOT,
    second_endpoint.connection_point_nr AS ENDPOINT_2_CONNECTION_POINT_NR,
    second_endpoint.slot AS ENDPOINT_2_SLOT,
    second_endpoint.subslot AS ENDPOINT_2_SUBSLOT,
    second_endpoint.location AS ENDPOINT_2_LOCATION,
    first_endpoint.endpoint_proof_source,
    first_endpoint.proof_priority,
    first_endpoint.endpoint_row_count AS ENDPOINT_ROW_COUNT,
    first_endpoint.duplicate_endpoint_count AS DUPLICATE_ENDPOINT_COUNT,
    first_endpoint.null_endpoint_count AS NULL_ENDPOINT_COUNT,
    0 AS AMBIGUITY_COUNT
FROM ranked_endpoints first_endpoint
JOIN ranked_endpoints second_endpoint
    ON second_endpoint.service_id = first_endpoint.service_id
    AND second_endpoint.edge_name = first_endpoint.edge_name
    AND second_endpoint.port_match_rule = first_endpoint.port_match_rule
    AND second_endpoint.endpoint_proof_source = first_endpoint.endpoint_proof_source
    AND second_endpoint.endpoint_rank = 2
WHERE first_endpoint.endpoint_rank = 1
)
SELECT *
FROM candidate_pairs
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY service_id, edge_name
    ORDER BY proof_priority, endpoint_proof_source
) = 1;
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.

INSERT INTO prod_all SELECT 'TRANSPORT_DEVICE_ADJACENCY', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'EDGE_NAME', EDGE_NAME,
    'LEVEL_NO', LEVEL_NO,
    'LEVEL', LEVEL_TAG,
    'PARENT_EDGE_NAME', PARENT_EDGE_NAME,
    'EDGE_POSITION', EDGE_POSITION,
    'EDGE_POSITION_ID', EDGE_POSITION_ID,
    'EDGE_POSITION_PATH', EDGE_POSITION_PATH,
    'PATH_TEXT', PATH_TEXT,
    'PORT_MATCH_RULE', PORT_MATCH_RULE,
    'PORT_MATCH_SOURCE_VIEW', PORT_MATCH_SOURCE_VIEW,
    'PORT_MATCH_SOURCE_IDS', PORT_MATCH_SOURCE_IDS,
    'PLATFORM_FAMILY', PLATFORM_FAMILY,
    'ENDPOINT_1_SITE_CODE', ENDPOINT_1_SITE_CODE,
    'ENDPOINT_1_SITE_TYPE', ENDPOINT_1_SITE_TYPE,
    'ENDPOINT_1_NE', ENDPOINT_1_NE,
    'ENDPOINT_1_NE_PART', ENDPOINT_1_NE_PART,
    'ENDPOINT_1_FUNCTION', ENDPOINT_1_FUNCTION,
    'ENDPOINT_1_DEVICE_CONNECTION_POINT_NR', ENDPOINT_1_DEVICE_CONNECTION_POINT_NR,
    'ENDPOINT_1_DEVICE_SLOT', ENDPOINT_1_DEVICE_SLOT,
    'ENDPOINT_1_DEVICE_SUBSLOT', ENDPOINT_1_DEVICE_SUBSLOT,
    'ENDPOINT_1_CCP_CONNECTION_POINT_NR', ENDPOINT_1_CCP_CONNECTION_POINT_NR,
    'ENDPOINT_1_CCP_SLOT', ENDPOINT_1_CCP_SLOT,
    'ENDPOINT_1_CCP_SUBSLOT', ENDPOINT_1_CCP_SUBSLOT,
    'ENDPOINT_1_CONNECTION_POINT_NR', ENDPOINT_1_CONNECTION_POINT_NR,
    'ENDPOINT_1_SLOT', ENDPOINT_1_SLOT,
    'ENDPOINT_1_SUBSLOT', ENDPOINT_1_SUBSLOT,
    'ENDPOINT_1_LOCATION', ENDPOINT_1_LOCATION,
    'ENDPOINT_2_SITE_CODE', ENDPOINT_2_SITE_CODE,
    'ENDPOINT_2_SITE_TYPE', ENDPOINT_2_SITE_TYPE,
    'ENDPOINT_2_NE', ENDPOINT_2_NE,
    'ENDPOINT_2_NE_PART', ENDPOINT_2_NE_PART,
    'ENDPOINT_2_FUNCTION', ENDPOINT_2_FUNCTION,
    'ENDPOINT_2_DEVICE_CONNECTION_POINT_NR', ENDPOINT_2_DEVICE_CONNECTION_POINT_NR,
    'ENDPOINT_2_DEVICE_SLOT', ENDPOINT_2_DEVICE_SLOT,
    'ENDPOINT_2_DEVICE_SUBSLOT', ENDPOINT_2_DEVICE_SUBSLOT,
    'ENDPOINT_2_CCP_CONNECTION_POINT_NR', ENDPOINT_2_CCP_CONNECTION_POINT_NR,
    'ENDPOINT_2_CCP_SLOT', ENDPOINT_2_CCP_SLOT,
    'ENDPOINT_2_CCP_SUBSLOT', ENDPOINT_2_CCP_SUBSLOT,
    'ENDPOINT_2_CONNECTION_POINT_NR', ENDPOINT_2_CONNECTION_POINT_NR,
    'ENDPOINT_2_SLOT', ENDPOINT_2_SLOT,
    'ENDPOINT_2_SUBSLOT', ENDPOINT_2_SUBSLOT,
    'ENDPOINT_2_LOCATION', ENDPOINT_2_LOCATION,
    'ENDPOINT_PROOF_SOURCE', ENDPOINT_PROOF_SOURCE,
    'ENDPOINT_ROW_COUNT', ENDPOINT_ROW_COUNT,
    'DUPLICATE_ENDPOINT_COUNT', DUPLICATE_ENDPOINT_COUNT,
    'NULL_ENDPOINT_COUNT', NULL_ENDPOINT_COUNT,
    'AMBIGUITY_COUNT', AMBIGUITY_COUNT
)
FROM prod_transport_device_adjacency_rows;
-- Role: prod_site_metadata_source_codes product metadata intermediate used to enrich final product rows.

----------------------------------------------------------------------
-- SITE_METADATA: Site metadata for all site types in this export
----------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE prod_site_metadata_source_codes AS
SELECT DISTINCT
    row_data:SITE_CODE::VARCHAR AS SITE_CODE,
    row_data:SITE_TYPE::VARCHAR AS SITE_TYPE,
    row_data:SITE_TYPE_NO::VARCHAR AS SITE_TYPE_NO
FROM prod_all
WHERE row_data:SITE_CODE IS NOT NULL
UNION
SELECT DISTINCT
    row_data:A_SITE_CODE::VARCHAR AS SITE_CODE,
    row_data:A_SITE_TYPE::VARCHAR AS SITE_TYPE,
    row_data:A_SITE_TYPE_NUMBER::VARCHAR AS SITE_TYPE_NO
FROM prod_all
WHERE row_data:A_SITE_CODE IS NOT NULL
UNION
SELECT DISTINCT
    row_data:B_SITE_CODE::VARCHAR AS SITE_CODE,
    row_data:B_SITE_TYPE::VARCHAR AS SITE_TYPE,
    row_data:B_SITE_TYPE_NUMBER::VARCHAR AS SITE_TYPE_NO
FROM prod_all
WHERE row_data:B_SITE_CODE IS NOT NULL;
-- Role: prod_site_metadata_rows product metadata intermediate used to enrich final product rows.

CREATE OR REPLACE TEMP TABLE prod_site_metadata_rows AS
SELECT DISTINCT
    s.SITE_CODE,
    s.SITE_TYPE,
    s.SITE_TYPE_NO,
    s.SITE_NAME,
    s.CITY,
    s.STATE,
    s.COUNTRY,
    s.STREET,
    s.POST_CODE,
    s.SITECATEGORY,
    s.CONSUMABLE,
    s.HUB,
    s.SITE_LOCATION_ID,
    s.BUILDING,
    s.GEO_LATITUDE,
    s.GEO_LONGITUDE
FROM prod_access_db.inca_src.V_T_INCATNT_SITE_CURRENT s
JOIN prod_site_metadata_source_codes source_codes
    ON s.SITE_CODE = source_codes.SITE_CODE
    AND (source_codes.SITE_TYPE IS NULL OR s.SITE_TYPE = source_codes.SITE_TYPE)
    AND (source_codes.SITE_TYPE_NO IS NULL OR NVL(s.SITE_TYPE_NO, '') = NVL(source_codes.SITE_TYPE_NO, ''));
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.

INSERT INTO prod_all SELECT 'SITE_METADATA', OBJECT_CONSTRUCT(
    'SITE_CODE', SITE_CODE,
    'SITE_TYPE', SITE_TYPE,
    'SITE_TYPE_NO', SITE_TYPE_NO,
    'SITE_NAME', SITE_NAME,
    'CITY', CITY,
    'STATE', STATE,
    'COUNTRY', COUNTRY,
    'STREET', STREET,
    'POST_CODE', POST_CODE,
    'SITECATEGORY', SITECATEGORY,
    'CONSUMABLE', CONSUMABLE,
    'HUB', HUB,
    'SITE_LOCATION_ID', SITE_LOCATION_ID,
    'BUILDING', BUILDING,
    'GEO_LATITUDE', GEO_LATITUDE,
    'GEO_LONGITUDE', GEO_LONGITUDE
)
FROM prod_site_metadata_rows;
-- Role: prod_site_location_rows product route-order metadata intermediate.

CREATE OR REPLACE TEMP TABLE prod_site_location_rows AS
SELECT SITE_CODE, SITE_TYPE, SITE_TYPE_NO, MAX(SITE_LOCATION_ID) AS SITE_LOCATION_ID
FROM prod_site_metadata_rows
GROUP BY SITE_CODE, SITE_TYPE, SITE_TYPE_NO;
-- Role: prod_route_order_relevant_edges product route-order metadata intermediate.

CREATE OR REPLACE TEMP TABLE prod_route_order_relevant_edges AS
SELECT DISTINCT
    row_data:SERVICE_ID::VARCHAR AS SERVICE_ID,
    row_data:ROUTE_PATH::VARCHAR AS ROUTE_PATH
FROM prod_all
WHERE qid IN ('TRUNK_ODF', 'DEVICE')
  AND row_data:SERVICE_ID IS NOT NULL
  AND row_data:ROUTE_PATH IS NOT NULL;
-- Role: prod_route_order_position_rows product route-order metadata intermediate.

----------------------------------------------------------------------
-- ROUTE_ORDER_METADATA: Snowflake-backed route order contract.
-- EDGE_SEQUENCE is derived from content-position order fields, never
-- route-name parsing, BFS traversal, source row order, or cable number.
----------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE prod_route_order_position_rows AS
SELECT
    walk.service_id,
    walk.edge_name,
    MIN(walk.edge_position) AS edge_position,
    MIN(walk.edge_position_id) AS edge_position_id
FROM prod_edge_walk walk
JOIN prod_route_order_relevant_edges relevant_edges
    ON relevant_edges.SERVICE_ID = walk.service_id
    AND relevant_edges.ROUTE_PATH = walk.edge_name
WHERE walk.edge_name IS NOT NULL
  AND walk.edge_position IS NOT NULL
GROUP BY walk.service_id, walk.edge_name;
-- Role: prod_route_order_site_sides product route-order side metadata intermediate.

CREATE OR REPLACE TEMP TABLE prod_route_order_site_sides AS
SELECT DISTINCT
    row_data:SERVICE_ID::VARCHAR AS service_id,
    row_data:ROUTE_PATH::VARCHAR AS route_path,
    row_data:SITE_CODE::VARCHAR AS site_code,
    row_data:SITE_SIDE::VARCHAR AS site_side
FROM prod_all
WHERE qid = 'TRUNK_ODF'
  AND row_data:SERVICE_ID IS NOT NULL
  AND row_data:ROUTE_PATH IS NOT NULL
  AND row_data:SITE_CODE IS NOT NULL
  AND row_data:SITE_SIDE IS NOT NULL;
-- Role: prod_route_order_metadata_rows product route-order contract rows.

CREATE OR REPLACE TEMP TABLE prod_route_order_metadata_rows AS
SELECT DISTINCT
    ranked.service_id AS SERVICE_ID,
    ranked.edge_name AS ROUTE_PATH,
    ranked.edge_sequence AS EDGE_SEQUENCE,
    ranked.edge_name AS EDGE_NAME,
    COALESCE(pcg.A_SITE_CODE, tx.A_SITE_CODE) AS A_SITE_CODE,
    COALESCE(pcg.A_SITE_TYPE, tx.A_SITE_TYPE) AS A_SITE_TYPE,
    COALESCE(pcg.A_SITE_TYPE_NUMBER, tx.A_SITE_TYPE_NUMBER) AS A_SITE_TYPE_NO,
    COALESCE(pcg.B_SITE_CODE, tx.B_SITE_CODE) AS B_SITE_CODE,
    COALESCE(pcg.B_SITE_TYPE, tx.B_SITE_TYPE) AS B_SITE_TYPE,
    COALESCE(pcg.B_SITE_TYPE_NUMBER, tx.B_SITE_TYPE_NUMBER) AS B_SITE_TYPE_NO,
    a_site.SITE_LOCATION_ID AS A_SITE_LOCATION_ID,
    b_site.SITE_LOCATION_ID AS B_SITE_LOCATION_ID,
    a_side.site_side AS A_SITE_SIDE,
    b_side.site_side AS B_SITE_SIDE,
    COALESCE(pcg.MEDIA, tx.PREFIX, tx.OBJECTTYPE) AS MEDIA
FROM (
    SELECT
        service_id,
        edge_name,
        DENSE_RANK() OVER (
            PARTITION BY service_id
            ORDER BY edge_position, edge_position_id
        ) AS edge_sequence
    FROM prod_route_order_position_rows
) ranked
LEFT JOIN prod_trunk_metadata_rows pcg
    ON pcg.BPK_PCG = ranked.edge_name
LEFT JOIN prod_transmission_metadata_rows tx
    ON tx.BPK_TRANSMISSION = ranked.edge_name
LEFT JOIN prod_site_location_rows a_site
    ON a_site.SITE_CODE = COALESCE(pcg.A_SITE_CODE, tx.A_SITE_CODE)
    AND a_site.SITE_TYPE = COALESCE(pcg.A_SITE_TYPE, tx.A_SITE_TYPE)
    AND NVL(a_site.SITE_TYPE_NO, '') = NVL(COALESCE(pcg.A_SITE_TYPE_NUMBER, tx.A_SITE_TYPE_NUMBER), '')
LEFT JOIN prod_site_location_rows b_site
    ON b_site.SITE_CODE = COALESCE(pcg.B_SITE_CODE, tx.B_SITE_CODE)
    AND b_site.SITE_TYPE = COALESCE(pcg.B_SITE_TYPE, tx.B_SITE_TYPE)
    AND NVL(b_site.SITE_TYPE_NO, '') = NVL(COALESCE(pcg.B_SITE_TYPE_NUMBER, tx.B_SITE_TYPE_NUMBER), '')
LEFT JOIN prod_route_order_site_sides a_side
    ON a_side.service_id = ranked.service_id
    AND a_side.route_path = ranked.edge_name
    AND a_side.site_code = COALESCE(pcg.A_SITE_CODE, tx.A_SITE_CODE)
    AND a_side.site_side = 'A'
LEFT JOIN prod_route_order_site_sides b_side
    ON b_side.service_id = ranked.service_id
    AND b_side.route_path = ranked.edge_name
    AND b_side.site_code = COALESCE(pcg.B_SITE_CODE, tx.B_SITE_CODE)
    AND b_side.site_side = 'B'
WHERE COALESCE(pcg.A_SITE_CODE, tx.A_SITE_CODE) IS NOT NULL
  AND COALESCE(pcg.B_SITE_CODE, tx.B_SITE_CODE) IS NOT NULL;
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.

INSERT INTO prod_all SELECT 'ROUTE_ORDER_METADATA', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'ROUTE_PATH', ROUTE_PATH,
    'EDGE_SEQUENCE', EDGE_SEQUENCE,
    'EDGE_NAME', EDGE_NAME,
    'A_SITE_CODE', A_SITE_CODE,
    'A_SITE_TYPE', A_SITE_TYPE,
    'A_SITE_TYPE_NO', A_SITE_TYPE_NO,
    'B_SITE_CODE', B_SITE_CODE,
    'B_SITE_TYPE', B_SITE_TYPE,
    'B_SITE_TYPE_NO', B_SITE_TYPE_NO,
    'A_SITE_LOCATION_ID', A_SITE_LOCATION_ID,
    'B_SITE_LOCATION_ID', B_SITE_LOCATION_ID,
    'A_SITE_SIDE', A_SITE_SIDE,
    'B_SITE_SIDE', B_SITE_SIDE,
    'MEDIA', MEDIA
)
FROM prod_route_order_metadata_rows
ORDER BY SERVICE_ID, EDGE_SEQUENCE;
-- Role: prod_dp_endpoint_role_candidates product demarcation endpoint role proof intermediate.

----------------------------------------------------------------------
-- DP_ENDPOINT_ROLE: demarcation endpoint role proof.
-- Emits only when a DP/SDP row maps to exactly one structured route
-- endpoint at the strongest available proof level. No route-name parsing.
----------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE prod_dp_endpoint_role_candidates AS
SELECT
    dp.SERVICE_ID,
    dp.ROUTE_PATH AS DP_ROUTE_PATH,
    dp.SITE_CODE,
    dp.SITE_TYPE,
    dp.SITE_TYPE_NO,
    dp.POS,
    dp.CABLING_POINTS,
    dp.CONN_TYPE,
    dp.NE_INFORMATION,
    dp.DP_OWNER,
    dp.CONN_POINT_INT_ID,
    rom.ROUTE_PATH AS MATCHED_ROUTE_PATH,
    rom.EDGE_SEQUENCE AS MATCHED_EDGE_SEQUENCE,
    'A' AS MATCHED_SITE_SIDE,
    'DP_EXACT_SITE_IDENTITY' AS ENDPOINT_PROOF_SOURCE,
    1 AS PROOF_PRIORITY
FROM prod_dp_sdp_rows dp
JOIN prod_route_order_metadata_rows rom
    ON rom.SERVICE_ID = dp.SERVICE_ID
    AND dp.SITE_CODE = rom.A_SITE_CODE
    AND dp.SITE_TYPE = rom.A_SITE_TYPE
    AND NVL(dp.SITE_TYPE_NO, '') = NVL(rom.A_SITE_TYPE_NO, '')

UNION ALL

SELECT
    dp.SERVICE_ID,
    dp.ROUTE_PATH AS DP_ROUTE_PATH,
    dp.SITE_CODE,
    dp.SITE_TYPE,
    dp.SITE_TYPE_NO,
    dp.POS,
    dp.CABLING_POINTS,
    dp.CONN_TYPE,
    dp.NE_INFORMATION,
    dp.DP_OWNER,
    dp.CONN_POINT_INT_ID,
    rom.ROUTE_PATH AS MATCHED_ROUTE_PATH,
    rom.EDGE_SEQUENCE AS MATCHED_EDGE_SEQUENCE,
    'B' AS MATCHED_SITE_SIDE,
    'DP_EXACT_SITE_IDENTITY' AS ENDPOINT_PROOF_SOURCE,
    1 AS PROOF_PRIORITY
FROM prod_dp_sdp_rows dp
JOIN prod_route_order_metadata_rows rom
    ON rom.SERVICE_ID = dp.SERVICE_ID
    AND dp.SITE_CODE = rom.B_SITE_CODE
    AND dp.SITE_TYPE = rom.B_SITE_TYPE
    AND NVL(dp.SITE_TYPE_NO, '') = NVL(rom.B_SITE_TYPE_NO, '')

UNION ALL

SELECT
    dp.SERVICE_ID,
    dp.ROUTE_PATH AS DP_ROUTE_PATH,
    dp.SITE_CODE,
    dp.SITE_TYPE,
    dp.SITE_TYPE_NO,
    dp.POS,
    dp.CABLING_POINTS,
    dp.CONN_TYPE,
    dp.NE_INFORMATION,
    dp.DP_OWNER,
    dp.CONN_POINT_INT_ID,
    rom.ROUTE_PATH AS MATCHED_ROUTE_PATH,
    rom.EDGE_SEQUENCE AS MATCHED_EDGE_SEQUENCE,
    'A' AS MATCHED_SITE_SIDE,
    'DP_SITE_CODE_TRANSPORT_ENDPOINT' AS ENDPOINT_PROOF_SOURCE,
    2 AS PROOF_PRIORITY
FROM prod_dp_sdp_rows dp
JOIN prod_route_order_metadata_rows rom
    ON rom.SERVICE_ID = dp.SERVICE_ID
    AND dp.SITE_CODE = rom.A_SITE_CODE
JOIN prod_transport_device_adjacency_rows transport_role
    ON transport_role.service_id = rom.SERVICE_ID
    AND transport_role.edge_name = rom.ROUTE_PATH
    AND (
        transport_role.ENDPOINT_1_SITE_CODE = dp.SITE_CODE
        OR transport_role.ENDPOINT_2_SITE_CODE = dp.SITE_CODE
    )
WHERE dp.DP_OWNER = 'ARELION'
  AND dp.CONN_POINT_INT_ID IS NOT NULL

UNION ALL

SELECT
    dp.SERVICE_ID,
    dp.ROUTE_PATH AS DP_ROUTE_PATH,
    dp.SITE_CODE,
    dp.SITE_TYPE,
    dp.SITE_TYPE_NO,
    dp.POS,
    dp.CABLING_POINTS,
    dp.CONN_TYPE,
    dp.NE_INFORMATION,
    dp.DP_OWNER,
    dp.CONN_POINT_INT_ID,
    rom.ROUTE_PATH AS MATCHED_ROUTE_PATH,
    rom.EDGE_SEQUENCE AS MATCHED_EDGE_SEQUENCE,
    'B' AS MATCHED_SITE_SIDE,
    'DP_SITE_CODE_TRANSPORT_ENDPOINT' AS ENDPOINT_PROOF_SOURCE,
    2 AS PROOF_PRIORITY
FROM prod_dp_sdp_rows dp
JOIN prod_route_order_metadata_rows rom
    ON rom.SERVICE_ID = dp.SERVICE_ID
    AND dp.SITE_CODE = rom.B_SITE_CODE
JOIN prod_transport_device_adjacency_rows transport_role
    ON transport_role.service_id = rom.SERVICE_ID
    AND transport_role.edge_name = rom.ROUTE_PATH
    AND (
        transport_role.ENDPOINT_1_SITE_CODE = dp.SITE_CODE
        OR transport_role.ENDPOINT_2_SITE_CODE = dp.SITE_CODE
    )
WHERE dp.DP_OWNER = 'ARELION'
  AND dp.CONN_POINT_INT_ID IS NOT NULL;
-- Role: prod_dp_endpoint_role_rows product demarcation endpoint role proof rows.

CREATE OR REPLACE TEMP TABLE prod_dp_endpoint_role_rows AS
WITH ranked_candidates AS (
    SELECT
        candidates.*,
        MIN(PROOF_PRIORITY) OVER (
            PARTITION BY SERVICE_ID, DP_ROUTE_PATH, SITE_CODE, SITE_TYPE, NVL(SITE_TYPE_NO, ''),
                POS, CABLING_POINTS, CONN_TYPE
        ) AS MIN_PROOF_PRIORITY,
        COUNT(*) OVER (
            PARTITION BY SERVICE_ID, DP_ROUTE_PATH, SITE_CODE, SITE_TYPE, NVL(SITE_TYPE_NO, ''),
                POS, CABLING_POINTS, CONN_TYPE, PROOF_PRIORITY
        ) AS SAME_PRIORITY_CANDIDATE_COUNT
    FROM prod_dp_endpoint_role_candidates candidates
)
SELECT
    SERVICE_ID,
    DP_ROUTE_PATH,
    SITE_CODE,
    SITE_TYPE,
    SITE_TYPE_NO,
    POS,
    CABLING_POINTS,
    CONN_TYPE,
    NE_INFORMATION,
    DP_OWNER,
    CONN_POINT_INT_ID,
    MATCHED_ROUTE_PATH,
    MATCHED_EDGE_SEQUENCE,
    MATCHED_SITE_SIDE,
    ENDPOINT_PROOF_SOURCE
FROM ranked_candidates
WHERE PROOF_PRIORITY = MIN_PROOF_PRIORITY
  AND SAME_PRIORITY_CANDIDATE_COUNT = 1;
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.

INSERT INTO prod_all SELECT 'DP_ENDPOINT_ROLE', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'DP_ROUTE_PATH', DP_ROUTE_PATH,
    'SITE_CODE', SITE_CODE,
    'SITE_TYPE', SITE_TYPE,
    'SITE_TYPE_NO', SITE_TYPE_NO,
    'POS', POS,
    'CABLING_POINTS', CABLING_POINTS,
    'CONN_TYPE', CONN_TYPE,
    'NE_INFORMATION', NE_INFORMATION,
    'DP_OWNER', DP_OWNER,
    'CONN_POINT_INT_ID', CONN_POINT_INT_ID,
    'MATCHED_ROUTE_PATH', MATCHED_ROUTE_PATH,
    'MATCHED_EDGE_SEQUENCE', MATCHED_EDGE_SEQUENCE,
    'MATCHED_SITE_SIDE', MATCHED_SITE_SIDE,
    'ENDPOINT_PROOF_SOURCE', ENDPOINT_PROOF_SOURCE
)
FROM prod_dp_endpoint_role_rows;
-- Role: prod_bo_fiber_device_names back-office fiber intermediate used to enrich final product rows.

----------------------------------------------------------------------
-- BO_FIBERS: Breakout fiber traces for notation enrichment (Phase 3+)
-- Used ONLY for notation context when BO ODF link is missing.
----------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE prod_bo_fiber_device_names AS
SELECT DISTINCT LOWER(TRIM(row_data:NE::VARCHAR)) AS NE_NAME
FROM prod_all
WHERE qid = 'DEVICE'
  AND row_data:NE IS NOT NULL;
-- Role: prod_bo_fiber_rows back-office fiber intermediate used to enrich final product rows.

CREATE OR REPLACE TEMP TABLE prod_bo_fiber_rows AS
SELECT DISTINCT
    bf.A_NE_NAME,
    bf.A_LOCATION,
    bf.A_CONNECTION_POINT,
    bf.A_CABLING_POINT,
    bf.B_LOCATION,
    bf.B_CONNECTION_POINT,
    bf.B_CABLING_POINT,
    bf.CABLE_ID,
    bf.FIBER_NO
FROM prod_access_db.inca_src.V_T_INCATNT_BO_FIBERS_CURRENT bf
JOIN prod_bo_fiber_device_names device_names
    ON LOWER(TRIM(SPLIT_PART(bf.A_NE_NAME, ',', 1))) = device_names.NE_NAME;
-- Role: prod_all product inventory QID/ROW_DATA rows combined into the final export.

INSERT INTO prod_all SELECT 'BO_FIBERS', OBJECT_CONSTRUCT(
    'A_NE_NAME', A_NE_NAME,
    'A_LOCATION', A_LOCATION,
    'A_CONNECTION_POINT', A_CONNECTION_POINT,
    'A_CABLING_POINT', A_CABLING_POINT,
    'B_LOCATION', B_LOCATION,
    'B_CONNECTION_POINT', B_CONNECTION_POINT,
    'B_CABLING_POINT', B_CABLING_POINT,
    'CABLE_ID', CABLE_ID,
    'FIBER_NO', FIBER_NO
)
FROM prod_bo_fiber_rows;

----------------------------------------------------------------------
-- Final combined export: Lasagna explicit-ID INCA rows only.
----------------------------------------------------------------------
WITH combined_export AS (
    SELECT 'COMBINED_00_RUN_METADATA' AS qid, OBJECT_CONSTRUCT(
        'REPORT_TYPE', 'lasagna_route_review',
        'GENERATED_AT_UTC', CURRENT_TIMESTAMP()::VARCHAR,
        'SOURCE_SQL', 'src/lasagna/snowflake/explicit_service_route_extract.sql'
    ) AS row_data_variant
    UNION ALL
    SELECT qid, row_data AS row_data_variant FROM prod_all
)
SELECT qid, TO_JSON(row_data_variant) AS row_data
FROM combined_export;
