contract PriorityQueue():
    def setup() -> bool: modifying
    def insert(_k: uint256) -> bool: modifying 
    def getMin() -> uint256: constant
    def delMin() -> uint256: modifying
    def getCurrentSize() -> uint256: constant

Deposit: event({_depositor: indexed(address), _depositBlock: indexed(uint256), _token: address, _amount: uint256})
ExitStarted: event({_exitor: indexed(address), _utxoPos: indexed(uint256), _token: address, _amount: uint256})
BlockSubmitted: event({_root: bytes32, _timestamp: timestamp})
TokenAdded: event({_token: address})

childChain: {
    root: bytes32,
    blockTimestamp: timestamp
}[uint256]

ExitingTx: {
    exitor: address,
    token: address,
    amount: uint256,
    inputCount: uint256
}

exits: {
    owner: address,
    token: address,
    amount: uint256
}[uint256]

exitsQueues: address[address]

CHILD_BLOCK_INTERVAL: uint256
ETH_ADDRESS: address

operator: address
currentChildBlock: uint256
currentDepositBlock: uint256
currentFeeExit: uint256


# @dev Constructor
@public
def __init__(_priorityQueueTemplate: address):
    assert _priorityQueueTemplate != ZERO_ADDRESS
    self.operator = msg.sender
    self.CHILD_BLOCK_INTERVAL = 1000
    self.ETH_ADDRESS = ZERO_ADDRESS
    self.currentChildBlock = self.CHILD_BLOCK_INTERVAL
    self.currentDepositBlock = 1
    self.currentFeeExit = 1    

    # Be careful, create_with_code_of currently doesn't support executing constructor.
    priorityQueue: address = create_with_code_of(_priorityQueueTemplate)    
    # Force executing as a constructor
    assert PriorityQueue(priorityQueue).setup()
    # ETH_ADDRESS means currently support only ETH.
    self.exitsQueues[self.ETH_ADDRESS] = priorityQueue

#
# Public Functions
#

# @dev Allows Plasma chain operator to submit block root.
# @params _root The root of a child chain block.
@public
def submitBlock(_root: bytes32):
    # Only operator can execute.
    assert msg.sender == self.operator
    self.childChain[currentChildBlock] = {
        root: _root,
        blockTimestamp: block.timestamp
    }

    # Update block numbers.
    self.currentChildBlock += self.CHILD_BLOCK_INTERVAL
    self.currentDepositBlock = 1

    log.BlockSubmitted(_root, block.timestamp) 

# @dev Allows anyone to deposit funds into the Plasma chain.
@public
@payable
def deposit():
    assert self.currentDepositBlock < self.CHILD_BLOCK_INTERVAL
    
    root: bytes32 = sha3(
                        concat(
                            convert(msg.sender, "bytes32"),
                            convert(self.ETH_ADDRESS, "bytes32"),
                            convert(msg.value, "bytes32")
                        )
    )                
    depositBlock: uint256 = getDepositBlock()

    self.childChain[depositBlock] = {
        root: root,
        blockTimestamp: block.timestamp
    }
    self.currentDepositBlock += 1

    log.Deposit(msg.sender, depositBlock, self.ETH_ADDRESS, msg.value)

# @dev Starts an exit from a deposit
# @param _depositPos UTXO position of the deposit
# @param _token Token type to deposit
# @param _amount Deposit amount
@public
def startDepositExit(_depositPos: uint256, _token: address, _amount: uint256):
    blknum: uint256 = _depositPos / 1000000000
    # Check that the given UTXO is a deposit
    assert blknum % self.CHILD_BLOCK_INTERVAL != 0

    root: bytes32 = self.childChain[blknum].root
    depositHash: bytes32 = sha3(
                                concat(
                                    convert(msg.sender, "bytes32"),
                                    convert(_token, "bytes32"),
                                    convert(_amount, "bytes32")
                                )
    )
    # Check that the block root of the UTXO position is same as depositHash.
    assert root == depositHash

    self.addExitToQueue(_depositPos, msg.sender, _token, _amount, self.childChain[blknum].blockTimestamp)

# @dev Allows the operator withdraw any allotted fees. Starts an exit to avoid theft.
# @param _token Token to withdraw.
# @param _amount Amount in fees to withdraw.
@public
def startFeeExit(_token: address, _amount: uint256):
    assert msg.sender == self.operator
    self.addExitToQueue(self.currentFeeExit, msg.sender, _token, _amount, block.timestamp)
    self.currentFeeExit += 1
    

# @dev Starts to exit a specified utxo.
@public
def startExit(_utxoPos: uint256, _txBytes: bytes[1024], _proof: bytes[1024], _sigs: bytes[1024]):
    blknum: uint256 = _utxoPos / 1000000000
    txindex: uint256 = (_utxoPos % 1000000000) / 10000
    oindex: uint256 = _utxoPos - blknum * 1000000000 - txindex * 10000

    exitingTx: {
        exitor: address,
        token: address,
        amount: uint256,
        inputCount: uint256
    } = self.createExitingTx(_txBytes, oindex)
    assert msg.sender == exitingTx.exitor

    root: bytes32 = self.childChain[blknum].root
    merkleHash: bytes32 = sha3(
                            concat(
                                sha3(_txBytes),
                                slice(_sigs, 0, 130)
                            )
    )

    assert self.checkSigs(sha3(_txBytes), root, exitingTx.inputCount, _sigs)
    assert self.checkMembership(txindex, root, _proof)

    self.addExitToQueue(_utxoPos, exitingTx.exitor, exitingTx.token, exitingTx.amount, childChain[blknum].blockTimestamp)

# @dev Allows anyone to challenge an exiting transaction by submitting proof of a double spend on the child chain.
@public
def challengeExit(_cUtxoPos: uint256, _eUtxoIndex: uint256, _txBytes: bytes[1024], _proof: bytes[1024], _sigs: bytes[1024], _confirmationSig: bytes[1024]):
    # The position of the exiting utxo
    eUtxoPos: uint256 = self.getUtxoPos(_txBytes, _eUtxoIndex)
    # The output position of the challenging utxo
    txindex: uint256 = (_cUtxoPos % 1000000000) / 10000
    # The block root of the challenging utxo
    root: bytes32 = self.childChain[_cUtxoPos / 1000000000].root
    # The hash of the challenging transaction
    txHash: bytes32 = sha3(_txBytes)

    confirmationHash: bytes32 = sha3(concat(txHash, root))
    merkleHash: bytes32 = sha3(concat(txHash, _sigs))
    # The owner of the exiting utxo
    owner: address = self.exits[eUtxoPos].owner
    
    # Check the owner of the exiting utxo is same as that of the confirmation signature to check a double spend.
    # if the utxo is a double spend, the confirmation signature was made by the owner of the exiting utxo.
    assert owner == self.checkSigs(confirmationHash, _confirmationSig)
    # Check the merkle proof of the transaction used to challenge
    assert self.checkMembership(txindex, root, _proof)

    # Delete the owner but keep the amount to prevent another exit
    self.exits[eUtxoPos].owner = ZERO_ADDRESS


# @dev Processes any exits that have completed the challenge period.
@public
def finalizeExits(_token: address):   
    nextExitArray: [uint256, uint256] = self.getNextExit(_token)
    utxoPos: uint256 = nextExitArray[0]
    exitable_at: uint256 = nextExitArray[1]

    currentExit: {
        owner: address,
        token: address,
        amount: uint256
    } = exits[utxoPos]
    for i in range(10000): # TODO: Right way? In addition, range() does not accept variable?
        if not exitable_at < block.timestamp:
            break
        currentExit = self.exits[utxoPos]
        
        # Allowed only ETH
        assert _token == ZERO_ADDRESS
        # Send the token amount of the exiting utxo to the owner of the utxo
        send(currentExit.owner, currentExit.amount)
        
        PriorityQueue(self.exitsQueues[_token]).delMin()
        # Delete owner of the utxo
        self.exits[utxoPos].owner = ZERO_ADDRESS

        if PriorityQueue(self.exitsQueues[_token]).getCurrentSize() > 0:
            [utxoPos, exitable_at] = self.getNextExit(_token)
        else:
            return


#
# Public view functions
#

# @dev Queries the child chain.
@public
@constant
def getChildChain(_blockNumber: uint256) -> (bytes32, uint256):
    return self.childChain[_blockNumber].root, self.childChain[_blockNumber].blockTimestamp

# @dev Determines the next deposit block number.
# @return Block number to be given to the next deposit block.
@public
@constant
def getDepositBlock() -> uint256:
    return self.currentChildBlock - self.CHILD_BLOCK_INTERVAL + self.currentDepositBlock

# @dev Returns information about an exit.
@public
@constant
def getExit(_utxoPos: uint256) -> (address, address, uint256):
    return self.exits[_utxoPos].owner, self.exits[_utxoPos].token, self.exits[_utxoPos].amount

# @dev Determines the next exit to be processed.
@public
@constant
def getNextExit(_token: address) -> (uint256, uint256):
    priority: uint256 = PriorityQueue(self.exitsQueues[_token]).getMin()
    utxoPos: uint256 = uint256(int128(priority))
    exitable_at: uint256 = shift(priority, 128)
    return utxoPos, exitable_at


#
# Private functions
#

# @dev Adds an exit to the exit queue.
@private
def addExitToQueue(_utxoPos: uint256, _exitor: address, _token: address, _amount: uint256, _created_at: uint256):
    assert self.exitsQueues[_token] != ZERO_ADDRESS

    # Maximum _created_at + 2 weeks or block.timestamp + 1 week
    exitable_at: int128 = max(int128(_created_at) + 2 * 7 * 24 * 60 * 60, int128(block.timestamp) + 1 * 7 * 24 * 60 * 60)
    # "priority" represents priority of　exitable_at over utxo position. 
    priority: uint256 = bitwise_or(shift(uint256(exitable_at), 128), _utxoPos)

    assert _amount > 0
    assert self.exits[_utxoPos].amount == 0

    assert PriorityQueue(self.exitsQueues[self.ETH_ADDRESS]).insert(priority)

    self.exits[_utxoPos] = {
        owner: _exitor,
        token: _token,
        amount: _amount
    }
    log.ExitStarted(msg.sender, _utxoPos, _token, _amount)


#
# Library
#

@private
@constant
def createExitingTx(_exitingTxBytes: bytes[1024], _oindex: uint256) -> exitingTx:
    # TxField: [blkbum1, txindex1, oindex1, blknum2, txindex2, oindex2, cur12, newowner1, amount1, newowner2, amount2, sig1, sig2]
    txList: [13] = RLPList(_exitingTxBytes, [uint256, uint256, uint256, uint256, uint256, uint256, address, address, uint256, address, uint256, bytes32, bytes32])
    return {
        exitor: txList[7 + _oindex * 2],
        token: txList[6],
        amount: txList[8 + _oindex * 2],
        inputCount: txList[0] * txList[3]
    }

@private
@constant
def getUtxoPos(_challengingTxBytes: bytes[1024], _oIndex: uint256) -> uint256:
    # TxField: [blkbum1, txindex1, oindex1, blknum2, txindex2, oindex2, cur12, newowner1, amount1, newowner2, amount2, sig1, sig2]
    txList: [13] = RLPList(_exitingTxBytes, [uint256, uint256, uint256, uint256, uint256, uint256, address, address, uint256, address, uint256, bytes32, bytes32])
    oIndexShift: uint256 = _oIndex * 3
    return txList[0 + oIndexShift] + txList[1 + oIndexShift] + txList[2 + oIndexShift]

@private
@constant
def checkSigs(_txHash: bytes32, _rootHash: bytes32, _blknum2: uint256, _sigs: bytes[1024]) -> bool:
    assert len(_sigs) % 65 == 0 and len(_sigs) <= 260
    sig1: bytes[1024] = slice(_sigs, 0, 65)
    sig2: bytes[1024] = slice(_sigs, 65, 65)
    confSig1: bytes[1024] = slice(_sigs, 130, 65)
    confirmationHash: bytes32 = sha3(concat(_txHash, _rootHash))

    check1: bool = true
    check2: bool = true

    check1 = self.ecrecoverSig(_txhash, sig1) == self.ecrecoverSig(confirmationHash, confSig1)
    if _blknum2 > 0:
        confSig2: bytes[1024] = slice(_sigs, 195, 65)
        check2 = self.ecrecoverSig(_txHash, sig2) == self.ecrecoverSig(confirmationHash, confSig2)

    return check1 and check2


@private
@constant
def ecrecoverSig(_txHash: bytes32, _sig: bytes[1024]) -> address:
    assert len(_sig) == 65
    # Perhaps convert() can only convert 'bytes' to 'int128', so in that case here should be fixed.
    r: uint256 = convert(slice(_sig, 0, 32), uint256)
    s: uint256 = convert(slice(_sig, 32, 32), uint256)
    v: uint256 = convert(slice(_sig, 64, 1), uint256)

    return ecrecover(_txHash, v, r, s)

@private
@constant
def checkMembership(_leaf: bytes32, _index: uint256, _rootHash: bytes32, _proof: bytes[1024]) -> bool:
    assert len(_proof) == 512
    proofElement: bytes32
    computedHash: bytes32 = _leaf

    # 16 = len(_proof) / 32
    for i in range(16):
        proofElement = slice(_proof, i * 32, 32)
        if _index % 2 == 0:
            computedHash = sha3(concat(computedHash, ploofElement))
        else:
            computedHash = sha3(concat(proofElement, computedHash))
        _index = _index / 2
    
    return computedHash == _rootHash
