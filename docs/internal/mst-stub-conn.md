If there is only one stub for a given block, we are done. So, we
consider two or more stubs connecting to a block.

If the block is feedthru for the layers of the stubs connecting to it,
then we are done, too.  So, we focus on two or more stubs with a
non-feedthru case. Let's start with two stubs. We have two cases to
consider: 

(case a) they are both H xor they are both V. First assume that they are on 
opposing faces (E and W, xor N and S). Note that the slide ranges
of those two segs may not intersect. In that case (a1) we must add a new
segment connected to both stubs (Note that we use the same solution
when they connect to the same face! In that case, we can't combine them
into one and we need the new segment to connect them).

The layer of the new segment can be the next layer up, if it exist. 
E.g., if both stubs are on M4, we use M5. If M5 is not defined, we use M3. 

The slide range of the new segment must be within the block. 
It's span will be determined by the two stubs
that it connects.

In the other case (a2), when there is a common slide, we
can combine the two stubs into one which inherits all the connections
(other segs + other busterms). 

(case b) Now, the second big case: one stub is H and the other is V. 
This is easy for us. We just connect the two logically
and stretch one or both physically (i.e. increase their span) so that
they connect physically, too. 

That completes the two cases for two stubs. If we have more stubs to connect,
i.e., three or more, we want to reduce them one stub at a time, 
until we get to these two base cases. Let's work it out for three stubs.

(case c) assume all are H (or V): if they are all one one face (rare but 
possible!) we do what we did in case (a1). Otherwise, we want to pick two to
combine into one seg as in case (a) so as to maximize the slide range,
i.e. we find the larger intersection. Then, we add a V segment to
connect to the other stubs.

(case d) Here is another case: we have three stubs, one H, two V (or dual
case: one V and two H). If we can combine the collinear stubs, we
apply the solution in (a2), and then we stretch the third stub to connect 
to the combined stub. Otherwise, the third stub is stretched to connect 
to the other two orthogonal stubs. 

I think that covers all of the two or three stub cases. If there are four or 
more stubs (even more rare, but possible), the idea is the same: reduce the
problem one stub at a time to a smaller problem. Look for collinear stubs 
that can be combined (their slides intersect and the intersection is wide 
enough), and look for orthogonal segs to connect to.

