The presentation contains 25 slides.

**Slide Descriptions**

  * **Slide 1: Assisted and Auto Bus Planning in Full-Chip Layout**
    This is the title slide.
  * **Slide 2: Outline**
    The presentation covers Introduction, Assisted bus planning, Auto bus planning, and Results, which includes Tanglewood, Manzano, and Nehalem.
  * **Slide 3: Introduction**
    The tools are a set of new interconnect (IC) planning tools within Galaxy - DT’s FCL tool, focusing on productivity and accurate top-down planning. Productivity goals include reducing full-chip planning from 2 weeks to 1 day and unit planning from 2 days to 10 minutes. Accurate planning means planning without layout/terminals, managing congestion, and planning down to lower hierarchy levels.
  * **Slide 4: Bus Planning**
    Input is the netlist and floor-plan, and output is interconnect as bus topologies (which are close to the exact layout). The two major flows are Assisted (helps designers craft topologies) and Auto (crafts and legalizes topologies); a combination of both is typically used.
  * **Slide 5: Assisted Bus Planning**
    This slide presents a flowchart for assisted bus planning, showing steps like Bus-term Generation, Bus Topology Generation, Bundling, Gridding, and adding wires, with user input required for selecting the topology.
  * **Slide 6: Topology Generation**
    The goal is to design a set of good topologies quickly, generating approximately 5000 topologies per minute. The output is a set of topologies describing a unique path for the bus from driver to receivers, without exact layout or coordinates.
  * **Slide 7: Topology Alternatives**
    This slide presents visual alternatives for topologies.
  * **Slide 8: Topology Alternatives**
    This slide presents visual alternatives for topologies.
  * **Slide 9: Auto Bus Planning**
    This flow builds on assisted planning to legalize topologies (eliminate DRVs) and choose topologies that reduce congestion. The two major flows are the Dilution based fast flow (25k nets per minute) and the Obstacle aware flow (1 bus per second).
  * **Slide 10: Dilution-based Flow**
    Dilution is non-minimum spacing between wires and accounts for power/clk grid density (a technique borrowed from Intel designers). It is used for early top-down planning, offering accurate congestion analysis and fast feedback to move/resize blocks or change bus topologies.
  * **Slide 11: Obstacle-aware Flow**
    This flow avoids all obstacles during wire placement. It supports planning buses in multiple hierarchy levels and bus planning when power/pre-routes exist, useful for late ECOs.
  * **Slide 12: Results**
    The tools have been used in projects like Tanglewood, Manzano, Nehalem, Tejas, and Whitefield.
  * **Slide 13: Tanglewood**
    This project involved a CPU core with three hierarchy levels (\~200 buses) and used assisted bus planning. The process involved selecting one topology per bus, choosing a location for each bus segment, and snapping to a grid.
  * **Slide 14: Tanglewood cont.**
    The result was that 95% of buses were planned using topologies, with a designer confirming the tools were "extremely helpful."
  * **Slide 15: Manzano**
    This project was a flat CPU core (25 blocks, 6k nets, \~300 buses) that used the dilution flow of auto bus planning to manage large RTL changes early in the design. The process involved assisted planning for 90% of buses and running the dilution flow to move blocks and reduce congestion.
  * **Slide 16: Manzano cont.**
    The flow continued with re-generating topologies and running the dilution flow in minutes, visually analyzing congestion, and snapping segments to routing tracks. The effort was reduced from 2 weeks of bussing to 1 day.
  * **Slide 17: Nehalem**
    This data was a unit (IEXEC) without terminals and deep hierarchy (5 levels), with two instances of ICLUST.
  * **Slide 18: Nehalem IEXEC Depth=0**
    This slide shows a block diagram of `iexec` containing `iclustr` and `iclustb`.
  * **Slide 19: Nehalem ICLUST Depth=0**
    This slide shows a block diagram of `iclustr(iclust)`.
  * **Slide 20: Nehalem ICLUST Depth=1**
    This slide shows a block diagram of `iclustr(iclust)`.
  * **Slide 21: Nehalem ICLUST Depth=2**
    This slide shows a block diagram of `iclustr(iclust)`.
  * **Slide 22: Nehalem IEXEC Depth=3**
    This slide shows a block diagram of `iexec` containing `iclustr` and `iclustb`.
  * **Slide 23: Nehalem cont.**
    This project used a combination of dilution and obstacle-aware flows, planning ICLUST buses first and then IEXEC buses with ICLUST buses acting as obstacles. Designer feedback reduced the planning effort from an estimated 2 days to only 10 minutes.
  * **Slide 24: Conclusion**
    The tools enable top-down planning of interconnect using bus topologies. Assisted planning provides a rich set of topologies (\~5000 per minute) and hierarchical design. Auto planning offers fast legalization (25k nets per minute), accurate congestion analysis, and early timing analysis.
  * **Slide 25: (Blank Title)**
    This is the Q\&A slide.

-----

**Summary of the Presentation**

  * New Galaxy-DT tools accelerate full-chip layout planning and productivity.
  * The tools facilitate top-down interconnect planning using bus topologies.
  * Assisted planning lets designers craft topologies; Auto planning legalizes them rapidly.
  * Auto planning uses dilution-based and obstacle-aware flows to manage congestion.
  * Case studies show planning time reduced from weeks to one day or minutes.
