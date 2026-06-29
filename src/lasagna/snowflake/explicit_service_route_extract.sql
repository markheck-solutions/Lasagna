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
       ccp.NE, ccp.NE_PART, ccp.FUNCTION AS OPTIC_FUNCTION,
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
WITH RECURSIVE edge_walk(level_no, level_tag, service_id, edge_name, edge_position, edge_position_id) AS (
    SELECT
        1 AS level_no,
        'L1' AS level_tag,
        SERVICE_ID,
        EDGE_NAME,
        EDGE_POSITION,
        EDGE_POSITION_ID
    FROM prod_edge_roots

    UNION ALL

    SELECT
        ew.level_no + 1 AS level_no,
        CONCAT('L', (ew.level_no + 1)::VARCHAR) AS level_tag,
        ew.SERVICE_ID,
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
    FROM edge_walk ew
    JOIN prod_access_db.inca_src.V_T_INCATNT_CONTENT_POSITION_CURRENT cp
        ON cp.CHILD_IDENTITY = ew.EDGE_NAME
    WHERE ew.level_no < 5
      AND COALESCE(cp.BFK_TRANSMISSION, cp.BFK_PCG) IS NOT NULL
)
SELECT level_no, level_tag, service_id, edge_name, edge_position, edge_position_id
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
-- TL_DEVICE: Map transport link names to device ports at each site.
-- Joins L1/L2 edge names to CCP where NE IS NOT NULL, resolving
-- which device (NE_PART) terminates each transport link at each site.
-- Used by inca_sorter.py to determine within-site building direction.
----------------------------------------------------------------------
INSERT INTO prod_all SELECT 'TL_DEVICE', OBJECT_CONSTRUCT(
    'SERVICE_ID', SERVICE_ID,
    'TL_NAME', TL_NAME,
    'SITE_CODE', SITE_CODE,
    'NE', NE,
    'NE_PART', NE_PART,
    'NEPART_SITE_CODE', NEPART_SITE_CODE,
    'NEPART_SITE_TYPE', NEPART_SITE_TYPE
) FROM (
    SELECT DISTINCT
        pe.SERVICE_ID,
        pe.EDGE_NAME AS TL_NAME,
        ccp.SITE_CODE,
        ccp.NE,
        ccp.NE_PART,
        TRIM(nep.NEPART_SITE_CODE) AS NEPART_SITE_CODE,
        nep.NEPART_SITE_TYPE
    FROM prod_edges pe
    JOIN prod_access_db.inca_src.V_T_INCATNT_CONTENT_CONNECTION_POINT_CURRENT ccp
        ON ccp.CONTENT = pe.EDGE_NAME
        AND ccp.NE IS NOT NULL
    LEFT JOIN prod_access_db.inca_src.V_T_INCATNT_NE_PART_CURRENT nep
        ON ccp.NE = nep.NE
        AND ccp.NE_PART = nep.NE_PART_NAME
    WHERE pe.LEVEL_TAG IN ('L1', 'L2')
);
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
    dp.CONTENT AS SERVICE_ID,
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
    p.B_SITE_CODE,
    p.B_SITE_TYPE,
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
    'B_SITE_CODE', B_SITE_CODE,
    'B_SITE_TYPE', B_SITE_TYPE,
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
    t.B_SITE_CODE,
    t.B_SITE_TYPE,
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
    'B_SITE_CODE', B_SITE_CODE,
    'B_SITE_TYPE', B_SITE_TYPE,
    'PREFIX', PREFIX
)
FROM prod_transmission_metadata_rows;
-- Role: prod_site_metadata_source_codes product metadata intermediate used to enrich final product rows.

----------------------------------------------------------------------
-- SITE_METADATA: Site metadata for all site types in this export
----------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE prod_site_metadata_source_codes AS
SELECT DISTINCT row_data:SITE_CODE::VARCHAR AS SITE_CODE
FROM prod_all
WHERE row_data:SITE_CODE IS NOT NULL;
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
    ON s.SITE_CODE = source_codes.SITE_CODE;
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
SELECT SITE_CODE, MAX(SITE_LOCATION_ID) AS SITE_LOCATION_ID
FROM prod_site_metadata_rows
GROUP BY SITE_CODE;
-- Role: prod_route_order_relevant_edges product route-order metadata intermediate.

CREATE OR REPLACE TEMP TABLE prod_route_order_relevant_edges AS
SELECT DISTINCT
    row_data:SERVICE_ID::VARCHAR AS SERVICE_ID,
    row_data:ROUTE_PATH::VARCHAR AS ROUTE_PATH
FROM prod_all
WHERE qid IN ('TRUNK_ODF', 'DEVICE', 'DP_SDP')
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
WHERE walk.level_no > 1
  AND walk.edge_name IS NOT NULL
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
    COALESCE(pcg.B_SITE_CODE, tx.B_SITE_CODE) AS B_SITE_CODE,
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
LEFT JOIN prod_site_location_rows b_site
    ON b_site.SITE_CODE = COALESCE(pcg.B_SITE_CODE, tx.B_SITE_CODE)
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
    'B_SITE_CODE', B_SITE_CODE,
    'A_SITE_LOCATION_ID', A_SITE_LOCATION_ID,
    'B_SITE_LOCATION_ID', B_SITE_LOCATION_ID,
    'A_SITE_SIDE', A_SITE_SIDE,
    'B_SITE_SIDE', B_SITE_SIDE,
    'MEDIA', MEDIA
)
FROM prod_route_order_metadata_rows
ORDER BY SERVICE_ID, EDGE_SEQUENCE;
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
