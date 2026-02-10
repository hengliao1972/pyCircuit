You need to follow the architecture spec to implement janus:

# First Step : Implementing BCC (Block Control Core)

## Folder and File Structure

- Janus
    - top.pyc
    - BCC 4-wide OOO superscalar core based on linx isa
        - IFU : f0.pyc, f1.pyc, f2.pyc, f3.pyc, f4.pyc, icache.pyc, ctrl.pyc
        - OOO : dec1.pyc, dec2.pyc, ren.pyc, s1.pyc, s2.pyc, rob.pyc, pc_buffer.pyc, flush_ctrl.pyc, renu.pyc
        - IEX : iex.pyc, iex_alu.pyc, iex_bru.pyc, iex_fsu.pyc, iex_agu.pyc, iex_std.pyc
        - BCtrl: bctrl.pyc bisq.pyc brenu.pyc, brob.pyc
        - LSU : liq.pyc, lhq.pyc, stq.pyc, scb.pyc, l1d.pyc
    - TMU
        - NOC : node.pyc pipe.pyc
        - SRAM : tilereg.pyc
    - TMA
        - tma.pyc
    - CUBE
        - cube.pyc
    - TAU Tile Arithmetic Unit (Accelerator)
        - tau.pyc

BCC packs a block ctrl command and send to different PEs (TMA, CUBE, TAU) and different PEs send it back after it completes a block instruction. BCC has a brob that records the status of each block ctrl command. All the PEs shares a tile register file. The tile register file called TMU and is shared between all the PEs though ring-based NOC. TAU is a SIMD^2 systolic core used for executing pto instructions such as (TADD, TSUB etc)



- BCC - Block Control Core # A full blown 4-wide ooo core based on linx isa
    - IFU - Instruction Fetch Unit
        - F0 Stage: Merge multiple PC sources from branch units, flush logic
        - F1 Stage:
            - ISide: Lookup Icache tags
            - BSide: Lookup micro Branch Target Buffer (BTB), Bimodal predication.
        - F2 Stage:
            - ISide: Icache data returns and create instruction bundles
            - BSide: TAGE branch prediction
        - F3 Stage:
            - ISide: Retrieve branch instructions from instruction bundle, put it in instruction buffer
            - BSide: Static Predication and generate intra flush
        - F4 Stage: Read from inst buffer, put aligned decode bundles for 4 instructions and send to D1 stage.
        - ICache: tag array, sram, refill logic to L2 Cache
    - OOO - Out-of-Order Execution Unit
        - D1 Stage: DEC1 - Decode from F4 stage decode bundles into UOPs
        - D2 Stage: DEC2 - Break instructions into simpler micro-uops (2 input 1 output)
        - D3 Stage: REN - Rename from decoded atag into ptags, alloc entry in ROB
        - S1 Stage: Read Ready table and alloc entry in issue queue, accept bypass wakeup
        - S2 Stage: Write into Issue Queue, Update ISSQ Payload, S2 Pick 
        - ROB - Reorder buffer, 256 entry, record all status of each instruction
        - PC Buffer - Record PCs for each instruction
        - Flush Control - Monitors the flush and genrate flush broadcast
        - Rename Unit
            - SMAP : [atag:ptag] for frontside of the rob
            - CMAP : [atag:ptag] for commitside of the rob
            - MAPQ : incremental mapping for each instruction, used for flush recovery
            - freelist : free ptags for allocation and free
    - IEX
        - Issue Queue: Separate IQ for ALUx2, FSUx1, AGUx2 (load, store address), STDx2(store data), Each IQ has one picker
        - Register File: 128 Entry for PTAGS, 128 Entry for T hand and U hand, Each RF has 8 read, 8 write
        - Ready Table: status for each ptag (ready, not ready, valid)
        - Pipelines: Separate Pipelines for ALUx2, FSUx1, AGUx2 (to LSU), STDx2(to LSU store buffer), Each IQ pick one intrusction and dispatch to Pipeline, Each pipeline has 2 read port, 1 write port.
            - ALU Pipeline (1 cycle) x 2
                - P1 Stage: pick one instruction from IQ
                - I1 Stage: read source ptag and read regfile
                - I2 Stage: read regfile data and merge from bypass results
                - E1 Stage: execution for 1-cycle latency
                - W1 Stage: write back stage, generate write back req to RF, forward bypass values to back to I2, send resolve signal
                - W2 Stage: write back stage 2, value written to RF, forward value to I2
            - CMD Pipeline x 1
                - P1 Stage: pick one instruction from IQ
                - I1 Stage: read source ptag and read regfile
                - I2 Stage: read regfile data and merge from bypass results
                - E1 Stage: performs tile rename based on B.IOT instruction
                - E2 Stage: insert into block issue queue and then retires
            - BRU Pipeline (1 cycle) x 1
                - P1 Stage: pick one instruction from IQ
                - I1 Stage: read source ptag and read regfile
                - I2 Stage: read regfile data and merge from bypass results
                - E1 Stage: execution for 1-cycle latency, validate branch results with predicated results
                - W1 Stage: generate flush signals to OOO and ROB, resolve instruction
            - FSU Pipeline (multi cycle) x 1 includes floating point and system related registers
                - P1 Stage: pick one instruction from IQ
                - I1 Stage: read source ptag and read regfile
                - I2 Stage: read regfile data and merge from bypass results
                - E1-En Stage: execution for n-cycle latency
                - W1 Stage: write back stage, generate write back req to RF, forward bypass values to back to I2, send resolve signal
                - W2 Stage: write back stage 2, value written to RF, forward value to I2
            - AGU Pipeline x 2
                - P1 Stage: pick one instruction from IQ
                - I1 Stage: read source ptag and read regfile
                - I2 Stage: read regfile data and merge from bypass results
                - E1 Stage: calculate load/store address, put into load/store queue
                - E2 Stage: Pick load/store queue and lookup tags, lookup TLB, lookup L1 D cache
                - E3 Stage: Cache Data Return select from multiple ways, wakeup successor instructions
                - E4 Stage: Cache ECC and sign extend, result forward from store buffer
                - W1 Stage: Load data write back stage, generate write back req to RF, forward bypass values to back to I2, send resolve signal
                - W2 Stage: Load data write back stage 2, value written to RF, forward value to I2
            - STD Pipeline x 2
                - P1 Stage: pick one instruction from IQ
                - I1 Stage: read source ptag and read regfile
                - I2 Stage: read regfile data and merge from bypass results
                - E1 Stage: put store data, put into store queue and resolve instruction
    - BCtrl: block control unit
        - BISQ - Block Issue Queue that dispatch block command to CUBE, VEC, TMA
        - BRENU: block rename unit
        - BROB: block reorder buffer
    - LSU - Load/Store Unit
        - LIQ: Load inflight queue, buffers on fly load for data return
        - LHQ: Load hit queue, buffers resolved load that need address conflict check with other load and stores, generate nuke flush
        - STQ: store queue, that waits data and address to be ready
        - SCB: store coalesing buffer, for buffering store that is not flushed
        - MDB: memory disabuguation buffer that record previous load/store dependence, if found conflict, then delay the load
        - L1 DCache: 64KB 4 way cache, refill data from L2, snoop channels from L2
        - MMU:
            TLB : translation lookaside buffer for reading page tables
            PageWalker: walk pages incase of TLB miss
    - CSU - Cache System Unit
        - CtrlPipe:
        - L2 Cache: