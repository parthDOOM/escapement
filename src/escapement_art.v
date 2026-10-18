/*
 * Layout-art macro: isolated Metal2 shapes, no devices and no connections.
 * SPDX-License-Identifier: Apache-2.0
 *
 * The cell itself comes from the MACROS block in src/config.json (GDS and LEF in
 * macro/escapement_art/). This port-less blackbox only places it: synthesis keeps the
 * instance, LVS abstracts it, and simulation sees an empty module.
 */
`default_nettype none
(* blackbox *)
module escapement_art;
endmodule
`default_nettype wire
